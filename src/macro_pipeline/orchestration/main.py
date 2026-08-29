import os
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date

import pandas as pd
import structlog
from opentelemetry import trace

from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FMP_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
    build_component,
    read_switch,
)
from macro_pipeline.data.av_client import AlphaVantageClient, AlphaVantageClientError
from macro_pipeline.data.fmp_client import FMPClient, FMPClientError
from macro_pipeline.data.fred_client import FREDClient
from macro_pipeline.data.macro import safe_build_macro_snapshot
from macro_pipeline.llm.client import (
    FALLBACK_HEADLINE,
    HEADLINE_PROMPT_VERSION,
    MODEL,
    LLMClient,
)
from macro_pipeline.llm.validator import (
    API_ERROR_REASON_PREFIX,
    TOOL_FAILURE_REASON,
    VALIDATOR_PROMPT_VERSION,
    ValidatorAgent,
)
from macro_pipeline.observability.logger import setup_observability
from macro_pipeline.publishers.linkedin_client import LinkedInClient
from macro_pipeline.publishers.x_client import XClient
from macro_pipeline.render.playwright_engine import PlaywrightEngine
from macro_pipeline.storage.r2_client import R2Client
from macro_pipeline.storage.state import StateDB
from macro_pipeline.telegram.bot import TelegramBot, TelegramBotError
from macro_pipeline.validators.engine import ValidationEngine, ValidationError
from macro_pipeline.validators.schemas import MacroSnapshot, WeeklyCloseData

logger = structlog.get_logger(__name__)

# Versión combinada de prompt para trazabilidad. Incluye el modelo: un mismo
# prompt sobre modelos distintos produce titulares distintos, así que sin él
# `prompt_version` no basta para reproducir un post histórico.
_PROMPT_VERSION = (
    f"headline={HEADLINE_PROMPT_VERSION}/validator={VALIDATOR_PROMPT_VERSION}"
    f"/model={MODEL}"
)


def _generic_headline(data: WeeklyCloseData) -> str:
    """El titular que publica el pipeline cuando la capa LLM no redacta.

    Vive acá y no dentro de la rama de degradación porque lo usan dos caminos
    que alertan distinto —la capa LLM caída, que avisa, y la capa LLM sin
    configurar, que no—, y la premisa con la que ADR-009 acepta degradar ahí es
    que «el bloque genérico lleva las cifras reales». Con dos copias esa
    premisa se puede volver falsa en una sola de ellas, que es donde nadie
    mira.
    """
    return (
        f"📊 Cierre de Mercado Semanal:\n"
        f"S&P500: {data.sp500_weekly_return * 100:+.2f}%\n"
        f"NASDAQ: {data.nasdaq_weekly_return * 100:+.2f}%"
    )


class MacroOrchestrator:
    """
    Coordinador central del MacroPipeline.
    Arquitectura: datos deterministas → validación → LLM auxiliar → HITL → publicación.
    Garantías:
    - Mock Data bloqueado en producción (ALLOW_MOCK_DATA=false).
    - Idempotencia parcial: si X publicó pero LinkedIn falló, el re-run
      solo publica LinkedIn (mismo día: el `event_id` lleva la fecha).
    - Una red por bandera: si falta una credencial de X, LinkedIn publica
      igual, y al revés. `PUBLISH_X` / `PUBLISH_LINKEDIN` apagan una red a
      propósito, en silencio.
    - post_ids persistidos inmediatamente tras cada canal.
    - Estados explícitos: in_progress, published, failed, expired.
    """

    def __init__(self, tracer: trace.Tracer | None = None) -> None:
        self.tracer = tracer
        logger.info("initializing_orchestrator")

        # Ningun componente con credenciales puede matar el constructor
        # (ADR-009, limitacion (d)). Los motivos se juntan aca y los reporta el
        # punto de decision de `run_weekly_close`, que es el primer sitio donde
        # existen a la vez el canal de aviso y el `event_id`. Con eso el orden
        # de construccion deja de ser logica: era la raiz del problema —FMP y
        # AV se construian antes que Telegram, asi que cuando reventaban no
        # habia con que avisar— y no solo su sintoma.
        #
        # Dos diccionarios y no uno: un switch con un valor invalido significa
        # que no se pudo leer la intencion del operador y eso aborta siempre;
        # una credencial ausente degrada o aborta segun el componente. Unirlos
        # obligaria a llevar una bandera al lado de cada motivo.
        self.switch_errors: dict[str, str] = {}
        self.component_errors: dict[str, str] = {}

        self.fmp = self._build("fmp", USE_FMP_VAR, FMPClient)
        self.av = self._build("av", USE_AV_VAR, AlphaVantageClient)
        self.fred = self._build("fred", USE_FRED_VAR, FREDClient)
        self.llm = self._build("anthropic", USE_ANTHROPIC_VAR, LLMClient)
        self.r2 = self._build("r2", USE_R2_VAR, R2Client)
        self.telegram = self._build("telegram", USE_TELEGRAM_VAR, TelegramBot)
        self.x_client = self._build("x", PUBLISH_X_VAR, XClient)
        self.linkedin = self._build("linkedin", PUBLISH_LINKEDIN_VAR, LinkedInClient)

        # `ValidatorAgent.__init__` solo guarda el cliente, asi que no puede
        # fallar por credenciales: sin generador no hay validador, y con
        # generador siempre se construye.
        self.validator_agent = (
            ValidatorAgent(self.llm) if self.llm is not None else None
        )

        # Motivo por el que el bloque macro no llego, o None. Es de
        # **ejecucion** —API caida, serie corta, dato rancio— y por eso no vive
        # en `component_errors`, que es de arranque. Los dos alertan, pero en
        # sitios distintos y con textos distintos.
        self.macro_error: str | None = None

        self.validator_engine = ValidationEngine()
        self.renderer = PlaywrightEngine()
        self.state = StateDB()

        # Guardia de Mock Data: por defecto bloqueado en producción
        self._allow_mock = os.environ.get("ALLOW_MOCK_DATA", "false").lower() == "true"

    def _build[T](self, name: str, var: str, factory: Callable[[], T]) -> T | None:
        """Lee el switch, construye si corresponde y anota el motivo si falla."""
        enabled, switch_error = read_switch(var)
        if switch_error is not None:
            # El log vive aca y no en `read_switch` porque aca es donde
            # convergen los tres desenlaces, y los tres tienen que verse igual
            # de bien: `build_component` ya loggea el apagado deliberado y la
            # credencial ausente, asi que sin esta linea el unico silencioso
            # seria el que significa que el operador tipeo algo ininteligible
            # — el mas alarmante de los tres. El aviso del punto de decision no
            # lo cubre: ese solo llega a Telegram, y solo si Telegram existe.
            logger.warning(
                "switch_not_readable", component=name, switch=var, reason=switch_error
            )
            self.switch_errors[var] = switch_error
            return None
        cliente, motivo = build_component(name, factory, enabled)
        if motivo is not None:
            self.component_errors[name] = motivo
        return cliente

    @property
    def x_ready(self) -> bool:
        """Derivada del cliente y no un atributo aparte, a proposito.

        Un atributo puede desincronizarse del cliente, y peor: un test puede
        ponerlo a mano para saltearse el mockeo. Es exactamente como el bug de
        `5ba7997` —publicar nada y marcar el evento como publicado— vivio
        detras de cuatro tests de integracion en verde.
        """
        return self.x_client is not None

    @property
    def linkedin_ready(self) -> bool:
        return self.linkedin is not None

    @property
    def r2_ready(self) -> bool:
        return self.r2 is not None

    @property
    def x_error(self) -> str | None:
        """Derivada de `component_errors` y no un atributo aparte.

        Mismo motivo que `x_ready`: una sola fuente de verdad, para que la
        alerta no pueda nombrar una red distinta de la que se rompio.
        """
        return self.component_errors.get("x")

    @property
    def linkedin_error(self) -> str | None:
        return self.component_errors.get("linkedin")

    def _publisher_failures(self) -> str:
        """Las redes rotas y su motivo, para el texto de la alerta.

        Una red apagada no aparece: no tiene motivo porque no es un fallo.
        """
        fallos = []
        if self.x_error:
            fallos.append(f"X: {self.x_error}")
        if self.linkedin_error:
            fallos.append(f"LinkedIn: {self.linkedin_error}")
        return "\n".join(fallos)

    def _fetch_macro_snapshot(self) -> MacroSnapshot | None:
        """
        Obtiene y valida el contexto macro de FRED.

        Devuelve None ante cualquier problema (sin key, API caída, serie corta,
        dato rancio o fuera de rango). El cierre semanal se publica igual: los
        índices son el contenido principal, el macro es contexto.

        Escribe `self.macro_error` con el motivo cuando el fallo **es** un
        fallo, y lo deja en None cuando FRED simplemente no está configurado:
        ADR-009 declara opcional al bloque macro, y un componente opcional que
        no participa no está degradando. Avisar cada semana de una
        configuración permanente es el ruido que hace que se deje de leer el
        aviso que importa.
        """
        # Se limpia siempre, y no solo se escribe en los caminos malos: un
        # reintento dentro del mismo proceso heredaría el motivo de la run
        # anterior y avisaría de algo ya resuelto.
        self.macro_error = None

        if self.fred is None:
            return None

        snapshot, motivo = safe_build_macro_snapshot(self.fred)
        if snapshot is None:
            self.macro_error = motivo
            return None

        try:
            self.validator_engine.validate_macro_snapshot(snapshot)
        except ValidationError as e:
            logger.warning("macro_snapshot_rejected_by_validator", reason=str(e))
            self.macro_error = f"El validador rechazó la cifra macro: {e}"
            return None

        return snapshot

    def _fetch_weekly_close(self) -> tuple[WeeklyCloseData, str]:
        """
        Extrae y calcula datos deterministas para el cierre semanal.
        Retorna (WeeklyCloseData, data_source) donde data_source es 'fmp',
        'av' o 'mock'.
        Lanza RuntimeError si se produce Mock Data y ALLOW_MOCK_DATA=false.
        """
        logger.info("orchestrator_fetching_data")
        data_source = "fmp"

        try:
            # Una fuente sin construir entra por la misma puerta que una fuente
            # caida: la cascada FMP -> AV -> mock ya existe justo para esto, y
            # sin la guarda un FMP ausente saldria como `AttributeError` —
            # atrapado igual por el `except` de abajo, pero ilegible en el log.
            if self.fmp is None:
                raise FMPClientError("El cliente de FMP no se pudo construir.")
            sp500_df = self.fmp.get_historical_prices("^GSPC")
            nasdaq_df = self.fmp.get_historical_prices("^IXIC")
        except Exception as e:
            logger.warning("fmp_failed_falling_back_to_av", error=str(e))
            data_source = "av"
            try:
                if self.av is None:
                    raise AlphaVantageClientError(
                        "El cliente de Alpha Vantage no se pudo construir."
                    )
                sp500_df = self.av.get_daily_prices("SPY", outputsize="compact")
                nasdaq_df = self.av.get_daily_prices("QQQ", outputsize="compact")
            except Exception as av_error:
                logger.warning("av_failed_falling_back_to_mock", error=str(av_error))
                data_source = "mock"

                if not self._allow_mock:
                    raise RuntimeError(
                        "Todas las fuentes de datos fallaron (FMP, AV). "
                        "Mock Data bloqueado en producción. "
                        "Set ALLOW_MOCK_DATA=true solo en desarrollo."
                    ) from av_error

                logger.warning("using_mock_data_development_only")
                dates = pd.date_range(end=pd.Timestamp.today(), periods=10, freq="B")
                sp500_df = pd.DataFrame(
                    {"date": dates, "close": [5100 + i * 10 for i in range(10)]}
                )
                nasdaq_df = pd.DataFrame(
                    {"date": dates, "close": [16000 + i * 30 for i in range(10)]}
                )

        if len(sp500_df) < 6 or len(nasdaq_df) < 6:
            raise ValueError(
                "Datos insuficientes para calcular retornos semanales "
                f"(SP500={len(sp500_df)} filas, NASDAQ={len(nasdaq_df)} filas)."
            )

        sp_last = sp500_df.iloc[-1]
        ndq_last = nasdaq_df.iloc[-1]

        # Calcular por fecha real para evitar sesgo con festivos
        sp_cutoff = sp_last["date"] - pd.tseries.offsets.BDay(5)
        ndq_cutoff = ndq_last["date"] - pd.tseries.offsets.BDay(5)

        sp_prev_candidates = sp500_df[sp500_df["date"] <= sp_cutoff]
        ndq_prev_candidates = nasdaq_df[nasdaq_df["date"] <= ndq_cutoff]

        if sp_prev_candidates.empty or ndq_prev_candidates.empty:
            raise ValueError("No hay datos suficientes para hace 5 días hábiles.")

        sp_prev = sp_prev_candidates.iloc[-1]
        ndq_prev = ndq_prev_candidates.iloc[-1]

        sp_return = (sp_last["close"] - sp_prev["close"]) / sp_prev["close"]
        ndq_return = (ndq_last["close"] - ndq_prev["close"]) / ndq_prev["close"]

        data = WeeklyCloseData(
            date=(
                sp_last["date"].date()
                if isinstance(sp_last["date"], pd.Timestamp)
                else date.today()
            ),
            sp500_close=float(sp_last["close"]),
            sp500_weekly_return=float(sp_return),
            nasdaq_close=float(ndq_last["close"]),
            nasdaq_weekly_return=float(ndq_return),
            macro=self._fetch_macro_snapshot(),
        )
        logger.info(
            "data_fetched",
            data_source=data_source,
            sp500_close=data.sp500_close,
            nasdaq_close=data.nasdaq_close,
            macro_included=data.macro is not None,
        )
        return data, data_source

    def run_weekly_close(self) -> None:
        """Pipeline completo de Cierre Semanal con idempotencia parcial."""
        logger.info("starting_weekly_close_pipeline")

        # Sin canal de aviso no hay run que valga: cada salida de aca abajo
        # —degradacion, abort, timeout— termina contandoselo a alguien por
        # Telegram, y una run que no puede avisar de nada es justo la que
        # ADR-009 no acepta. Es una asercion y no un abort con motivo porque el
        # abort pertenece al punto de decision, que es donde el motivo se puede
        # reportar; aca solo se deja escrita la precondicion.
        assert self.telegram is not None

        span_ctx = (
            self.tracer.start_as_current_span("weekly_close_pipeline")
            if self.tracer
            else nullcontext()
        )

        # Fuera del `try`: el `except` general lo necesita para cerrar la fila,
        # y una variable que puede estar sin asignar ahi seria un fallo dentro
        # del manejador de fallos.
        event_id = f"weekly_close_{date.today()}"

        with span_ctx as span:
            try:
                # ── Guardia de duplicados ──────────────────────────────────────
                if self.state.is_published(event_id):
                    logger.info("event_already_published_skipping", event_id=event_id)
                    return

                # ── Sin ninguna red no hay cierre semanal ──────────────────────
                # Va antes del lock y antes de tocar el estado: si no se puede
                # publicar, todo lo que sigue —renderizar, llamar al LLM, pedir
                # aprobación a un humano— es trabajo tirado, y terminaba peor
                # que tirado. `mark_as_published` estaba al mismo nivel que el
                # `if self.publishers_ready`, no dentro, así que la run se
                # cerraba marcando como publicado un evento que no se publicó
                # en ninguna red (`5ba7997`).
                # Basta con **una** red viva: que falte la credencial de una no
                # es motivo para no publicar en la otra (ADR-009 — se degrada
                # cuando el fallo solo cuesta contexto).
                # No se toca el estado a propósito: sin fila, la próxima run
                # reintenta sola.
                if not (self.x_ready or self.linkedin_ready):
                    # Sin fallos y sin redes listas solo puede significar que
                    # estan las dos apagadas a proposito: `build_component`
                    # devuelve motivo `None` para una red apagada. Una sola
                    # fuente de verdad, para que no puedan divergir.
                    fallos = self._publisher_failures()
                    if not fallos:
                        logger.info("no_publishers_enabled", event_id=event_id)
                        return
                    logger.error("publishers_not_ready_aborting", event_id=event_id)
                    self.telegram.send_alert(
                        "⚠️ El cierre semanal no se ejecutó: no hay ninguna red "
                        "en condiciones de publicar.\n\n"
                        f"{fallos}\n\n"
                        "No se publicó nada y el evento queda sin marcar, así "
                        "que la próxima run lo reintenta. Verificar con "
                        "`python scripts/check_publishers.py`."
                    )
                    return

                # ── Locking ligero: evitar runs simultáneas ────────────────────
                if self.state.is_in_progress(event_id):
                    logger.warning(
                        "pipeline_already_running_skipping", event_id=event_id
                    )
                    return

                self.state.mark_in_progress(event_id)

                # ── Estado previo para reconciliación parcial ──────────────────
                prev_state = self.state.get_publication_state(event_id)
                x_already_done = bool(prev_state.get("x_post_id"))
                linkedin_already_done = bool(prev_state.get("linkedin_post_id"))

                # ── FASE DE DATOS ──────────────────────────────────────────────
                with (
                    self.tracer.start_as_current_span("ingesta_datos")
                    if self.tracer
                    else nullcontext()
                ):
                    data, data_source = self._fetch_weekly_close()

                # ── FASE DE VALIDACIÓN ─────────────────────────────────────────
                with (
                    self.tracer.start_as_current_span("validacion")
                    if self.tracer
                    else nullcontext()
                ):
                    self.validator_engine.validate_weekly_close(data)

                # ── FASE DE RENDERIZADO ────────────────────────────────────────
                with (
                    self.tracer.start_as_current_span("render")
                    if self.tracer
                    else nullcontext()
                ):
                    image_bytes = self.renderer.render_weekly_close(data)

                # ── FASE LLM ───────────────────────────────────────────────────
                if self.llm is None or self.validator_agent is None:
                    # ADR-009, tercer eje: ADR-001 declara auxiliar a la capa
                    # LLM, así que sin key no participa — y no participar no es
                    # degradar. Sin alerta, por el mismo motivo que FRED sin
                    # key. La asimetría con la API caída (que sí alerta) es el
                    # punto: si llega una alerta, es porque algo se rompió.
                    #
                    # No se abre el span `llm_headline`: no hubo llamada.
                    logger.info("llm_layer_not_participating", event_id=event_id)
                    headline = _generic_headline(data)
                    validator_approved = None
                    prompt_version = None
                else:
                    with (
                        self.tracer.start_as_current_span("llm_headline")
                        if self.tracer
                        else nullcontext()
                    ):
                        data_str = (
                            f"SP500: Cierre {data.sp500_close:,.2f} "
                            "(Retorno Semanal: "
                            f"{data.sp500_weekly_return * 100:+.2f}%)\n"
                            f"NASDAQ: Cierre {data.nasdaq_close:,.2f} "
                            "(Retorno Semanal: "
                            f"{data.nasdaq_weekly_return * 100:+.2f}%)"
                        )
                        if data.macro:
                            data_str += (
                                f"\nContexto macro (FRED):\n"
                                f"IPC interanual: {data.macro.cpi_yoy * 100:+.1f}% "
                                f"(dato de {data.macro.cpi_as_of:%m/%Y})\n"
                                f"Desempleo: {data.macro.unemployment_rate:.1f}% "
                                f"(dato de {data.macro.unrate_as_of:%m/%Y})\n"
                                f"Treasury 10 años: {data.macro.treasury_10y:.2f}% "
                                f"(dato de {data.macro.dgs10_as_of:%d/%m/%Y})"
                            )
                        headline = self.llm.generate_headline(data_str)
                        review = self.validator_agent.review_draft(headline, data_str)
                        validator_approved = bool(review.get("approved"))
                        review_reason = str(review.get("reason", ""))
                        generator_fell_back = headline == FALLBACK_HEADLINE

                        # Tres degradaciones distintas terminaban en el mismo
                        # aviso, y el aviso nombraba la unica que no siempre era.
                        # El `except` del agente validador devuelve
                        # `approved=False` igual que un rechazo real, asi que sin
                        # mirar el motivo no se distingue un prompt malo de la API
                        # caida — y esa es la diferencia entre tocar codigo y
                        # relanzar.
                        if generator_fell_back:
                            # El generador murio. Es el caso mas silencioso de los
                            # tres: su fallback no lleva ninguna cifra, asi que el
                            # validador —que busca cifras inventadas— lo aprueba,
                            # no habia rechazo, no habia aviso, y se publicaba
                            # "Cierre Semanal: Resumen del Mercado" a secas.
                            degradation = (
                                "⚠️ El generador de titulares no respondió (la API "
                                "de Anthropic falló); se publica el bloque "
                                "genérico con las cifras reales."
                            )
                            degradation_cause = "generador_caido"
                        elif not validator_approved and (
                            review_reason.startswith(API_ERROR_REASON_PREFIX)
                            or review_reason == TOOL_FAILURE_REASON
                        ):
                            degradation = (
                                "⚠️ El validador no llegó a revisar el titular (la "
                                "API de Anthropic falló); se publica el bloque "
                                f"genérico.\n\n"
                                f"Motivo: {review_reason}\n\n"
                                f"Titular sin verificar: {headline}"
                            )
                            degradation_cause = "validador_no_respondio"
                        elif not validator_approved:
                            degradation = (
                                "⚠️ El validador rechazó el titular generado; se "
                                f"publica el bloque genérico.\n\n"
                                f"Motivo: {review_reason}\n\n"
                                f"Titular descartado: {headline}"
                            )
                            degradation_cause = "titular_rechazado"
                        else:
                            degradation = ""
                            degradation_cause = ""

                        if degradation:
                            logger.error(
                                "llm_layer_degraded",
                                event_id=event_id,
                                cause=degradation_cause,
                                approved=validator_approved,
                                reason=review_reason,
                            )
                            # El log solo no alcanza: el operador recibe igual la
                            # peticion de aprobacion y el bloque generico se parece
                            # bastante a un cierre normal, asi que un fallo de la
                            # capa LLM puede repetirse semanas sin que nadie lo
                            # note. Es lo que paso con el reetiquetado que encontro
                            # el contract test (ver ADR-001). El aviso va antes de
                            # pedir aprobacion para que llegue en ese orden.
                            self.telegram.send_alert(degradation)
                            headline = _generic_headline(data)
                    prompt_version = _PROMPT_VERSION

                # ── Degradación: una red rota y la otra viva ───────────────────
                # Va antes de pedir aprobación, igual que el aviso de la capa
                # LLM y por el mismo motivo: quien aprueba tiene que saber que
                # ese cierre sale en una sola red.
                # Una red *apagada* no llega acá con `error` cargado: apagarla
                # es una decisión, no un fallo, y alertar cada semana por una
                # decisión propia es el ruido que hace que se dejen de leer las
                # alertas. Si llega una alerta, es porque algo se rompió.
                # Acá hay como mucho un error: si las dos estuvieran rotas, la
                # guarda pre-lock ya habría abortado.
                if self.x_error or self.linkedin_error:
                    caida = "X" if self.x_error else "LinkedIn"
                    viva = "LinkedIn" if self.x_error else "X"
                    logger.warning(
                        "publisher_degraded",
                        event_id=event_id,
                        down=caida,
                        up=viva,
                    )
                    self.telegram.send_alert(
                        f"⚠️ El cierre semanal sale solo en {viva}: el cliente "
                        f"de {caida} no se pudo construir.\n\n"
                        f"Motivo: {self.x_error or self.linkedin_error}\n\n"
                        "El cierre se publica igual si lo aprobás. Verificar con "
                        "`python scripts/check_publishers.py`."
                    )

                # ── Degradación: el bloque macro no llegó ──────────────────────
                # Mismo lugar y mismo motivo que los dos avisos de arriba: quien
                # aprueba tiene que saber que ese cierre sale con menos.
                # `macro_error` está cargado sólo cuando el bloque macro se
                # rompió. FRED sin key no llega acá con motivo: ADR-009 lo
                # declara opcional, y un opcional sin configurar no participa —
                # no degrada, así que no hay nada que avisar.
                if self.macro_error:
                    logger.warning(
                        "macro_degraded", event_id=event_id, reason=self.macro_error
                    )
                    self.telegram.send_alert(
                        "⚠️ El cierre semanal sale sin bloque macro.\n\n"
                        f"Motivo: {self.macro_error}\n\n"
                        "El cierre se publica igual si lo aprobás: los índices "
                        "son el contenido principal y el macro es contexto."
                    )

                # ── FASE HITL ──────────────────────────────────────────────────
                with (
                    self.tracer.start_as_current_span("hitl_telegram")
                    if self.tracer
                    else nullcontext()
                ):
                    logger.info("requesting_human_approval", event_id=event_id)
                    msg_id = self.telegram.send_approval_request(
                        text=headline, image_bytes=image_bytes
                    )

                    try:
                        approved = self.telegram.wait_for_approval(
                            msg_id, timeout_seconds=3600
                        )
                    except TelegramBotError:
                        self.state.mark_expired(event_id)
                        logger.error(
                            "pipeline_expired_telegram_timeout", event_id=event_id
                        )
                        return

                # ── FASE DE PUBLICACIÓN ────────────────────────────────────────
                if approved:
                    logger.info("pipeline_approved_publishing", event_id=event_id)

                    image_url = None
                    # `is not None` y no `self.r2_ready` por lo mismo que en la
                    # fase de publicacion: `mypy --strict` estrecha el tipo con
                    # lo primero y no con una propiedad.
                    if self.r2 is not None:
                        try:
                            image_url = self.r2.upload_image(
                                image_bytes, f"{event_id}.png"
                            )
                        # Ancho a proposito: `upload_image` solo convierte
                        # `ClientError` en `R2ClientError`, asi que un corte de
                        # red sale como `EndpointConnectionError` de botocore y
                        # atrapar solo lo nuestro dejaria abierto justo el
                        # fallo mas probable. R2 es opcional (ADR-007) y nada
                        # de lo que sigue depende de `image_url`: la unica
                        # forma de que un componente opcional tumbe la run
                        # —encima despues de que el humano aprobo y antes de
                        # publicar en ninguna red— es un bug, no una politica.
                        except Exception as e:
                            logger.error(
                                "r2_upload_failed_degrading",
                                event_id=event_id,
                                error=str(e),
                            )
                            self.telegram.send_alert(
                                "⚠️ La subida del snapshot a R2 falló; el "
                                "cierre se publica igual, sin copia remota de "
                                "la imagen.\n\n"
                                f"Motivo: {e}"
                            )

                    with (
                        self.tracer.start_as_current_span("publish")
                        if self.tracer
                        else nullcontext()
                    ):
                        # Cada red mira su propio cliente. `is not None` y no
                        # `self.x_ready` porque `mypy --strict` estrecha el tipo
                        # con lo primero y no con lo segundo.
                        # La reconciliación por `post_id` no se reemplaza: se
                        # combina. Una red puede estar sin publicar por dos
                        # motivos distintos —no está lista, o ya salió en un
                        # intento anterior— y son independientes.
                        if self.x_client is None:
                            logger.info("x_not_publishing", event_id=event_id)
                        elif x_already_done:
                            logger.info(
                                "x_already_published_skipping", event_id=event_id
                            )
                        else:
                            x_result = self.x_client.post_tweet(headline)
                            x_post_id = x_result.get("data", {}).get("id", "unknown")
                            self.state.mark_x_published(event_id, x_post_id)

                        if self.linkedin is None:
                            logger.info("linkedin_not_publishing", event_id=event_id)
                        elif linkedin_already_done:
                            logger.info(
                                "linkedin_already_published_skipping",
                                event_id=event_id,
                            )
                        else:
                            li_result = self.linkedin.post_text(headline)
                            li_post_id = li_result.get("id", "unknown")
                            self.state.mark_linkedin_published(event_id, li_post_id)

                    # Marcar como completamente publicado con todos los metadatos
                    self.state.mark_as_published(
                        event_id,
                        image_url=image_url,
                        data_source=data_source,
                        sp500_close=data.sp500_close,
                        nasdaq_close=data.nasdaq_close,
                        sp500_return=data.sp500_weekly_return,
                        nasdaq_return=data.nasdaq_weekly_return,
                        prompt_version=prompt_version,
                        headline=headline,
                        validator_approved=validator_approved,
                        macro=data.macro,
                    )
                    logger.info("pipeline_completed_successfully", event_id=event_id)
                else:
                    self.state.mark_failed(event_id, reason="rejected_by_human")
                    logger.warning("pipeline_aborted_by_human", event_id=event_id)

            except Exception as e:
                logger.error(
                    "pipeline_failed_critically", event_id=event_id, error=str(e)
                )
                # Toda excepcion deja un estado terminal (ADR-009). Sin esto la
                # fila se quedaba en `in_progress` para siempre y el reintento
                # del mismo `event_id` moria en el guard de duplicados de mas
                # arriba: ese cierre no salia nunca y la idempotencia parcial
                # que promete el docstring era inalcanzable.
                # `mark_failed` es un UPDATE acotado, asi que sirve para las
                # tres situaciones sin preguntar en cual estamos: si el abort
                # fue anterior al lock no hay fila y no se crea ninguna, y si
                # la excepcion llego despues de publicar no desmarca nada.
                self.state.mark_failed(event_id, reason=str(e))
                if span and hasattr(span, "record_exception"):
                    span.record_exception(e)
                raise


if __name__ == "__main__":
    import logging

    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(format="%(message)s", level=logging.INFO)

    tracer = setup_observability()
    orchestrator = MacroOrchestrator(tracer=tracer)
    orchestrator.run_weekly_close()
