import os
from contextlib import nullcontext
from datetime import date

import pandas as pd
import structlog
from opentelemetry import trace

from macro_pipeline.data.av_client import AlphaVantageClient
from macro_pipeline.data.fmp_client import FMPClient
from macro_pipeline.data.fred_client import FREDClient
from macro_pipeline.data.macro import safe_build_macro_snapshot
from macro_pipeline.llm.client import HEADLINE_PROMPT_VERSION, MODEL, LLMClient
from macro_pipeline.llm.validator import VALIDATOR_PROMPT_VERSION, ValidatorAgent
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


class MacroOrchestrator:
    """
    Coordinador central del MacroPipeline.
    Arquitectura: datos deterministas → validación → LLM auxiliar → HITL → publicación.
    Garantías:
    - Mock Data bloqueado en producción (ALLOW_MOCK_DATA=false).
    - Idempotencia parcial: si X publicó pero LinkedIn falló, el re-run
      solo publica LinkedIn.
    - post_ids persistidos inmediatamente tras cada canal.
    - Estados explícitos: in_progress, published, failed, expired.
    """

    def __init__(self, tracer: trace.Tracer | None = None) -> None:
        self.tracer = tracer
        logger.info("initializing_orchestrator")

        self.fmp = FMPClient()
        self.av = AlphaVantageClient()
        self.validator_engine = ValidationEngine()

        # FRED alimenta el bloque macro, que es complementario: sin key el
        # pipeline publica igual, solo que sin contexto macroeconómico.
        self.fred: FREDClient | None
        try:
            self.fred = FREDClient()
        except ValueError as e:
            logger.warning("fred_not_configured", reason=str(e))
            self.fred = None

        self.llm = LLMClient()
        self.validator_agent = ValidatorAgent(self.llm)
        self.renderer = PlaywrightEngine()

        self.telegram = TelegramBot()
        self.state = StateDB()

        try:
            self.r2 = R2Client()
            self.r2_ready = True
        except ValueError as e:
            logger.warning("r2_not_configured", reason=str(e))
            self.r2_ready = False

        try:
            self.x_client = XClient()
            self.linkedin = LinkedInClient()
            self.publishers_ready = True
        except ValueError as e:
            logger.warning("publishers_not_configured", reason=str(e))
            self.publishers_ready = False

        # Guardia de Mock Data: por defecto bloqueado en producción
        self._allow_mock = os.environ.get("ALLOW_MOCK_DATA", "false").lower() == "true"

    def _fetch_macro_snapshot(self) -> MacroSnapshot | None:
        """
        Obtiene y valida el contexto macro de FRED.

        Devuelve None ante cualquier problema (sin key, API caída, serie corta,
        dato rancio o fuera de rango). El cierre semanal se publica igual: los
        índices son el contenido principal, el macro es contexto.
        """
        if self.fred is None:
            return None

        snapshot = safe_build_macro_snapshot(self.fred)
        if snapshot is None:
            return None

        try:
            self.validator_engine.validate_macro_snapshot(snapshot)
        except ValidationError as e:
            logger.warning("macro_snapshot_rejected_by_validator", reason=str(e))
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
            sp500_df = self.fmp.get_historical_prices("^GSPC")
            nasdaq_df = self.fmp.get_historical_prices("^IXIC")
        except Exception as e:
            logger.warning("fmp_failed_falling_back_to_av", error=str(e))
            data_source = "av"
            try:
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

        span_ctx = (
            self.tracer.start_as_current_span("weekly_close_pipeline")
            if self.tracer
            else nullcontext()
        )

        with span_ctx as span:
            try:
                event_id = f"weekly_close_{date.today()}"

                # ── Guardia de duplicados ──────────────────────────────────────
                if self.state.is_published(event_id):
                    logger.info("event_already_published_skipping", event_id=event_id)
                    return

                # ── Sin publicadores no hay cierre semanal ─────────────────────
                # Va antes del lock y antes de tocar el estado: si no se puede
                # publicar, todo lo que sigue —renderizar, llamar al LLM, pedir
                # aprobación a un humano— es trabajo tirado, y terminaba peor
                # que tirado. `mark_as_published` estaba al mismo nivel que el
                # `if self.publishers_ready`, no dentro, así que la run se
                # cerraba marcando como publicado un evento que no se publicó
                # en ninguna red; la semana siguiente `is_published` lo daba por
                # hecho y ese cierre no salía nunca. Un `logger.warning` al
                # arrancar era lo único que lo decía.
                # No se toca el estado a propósito: sin fila, la próxima run
                # reintenta sola.
                if not self.publishers_ready:
                    logger.error("publishers_not_ready_aborting", event_id=event_id)
                    self.telegram.send_alert(
                        "⚠️ El cierre semanal no se ejecutó: falta alguna "
                        "credencial de X o LinkedIn y los clientes de "
                        "publicación no se pudieron construir.\n\n"
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
                with (
                    self.tracer.start_as_current_span("llm_headline")
                    if self.tracer
                    else nullcontext()
                ):
                    data_str = (
                        f"SP500: Cierre {data.sp500_close:,.2f} "
                        f"(Retorno Semanal: {data.sp500_weekly_return * 100:+.2f}%)\n"
                        f"NASDAQ: Cierre {data.nasdaq_close:,.2f} "
                        f"(Retorno Semanal: {data.nasdaq_weekly_return * 100:+.2f}%)"
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

                    if not validator_approved:
                        logger.error(
                            "draft_rejected_by_ai", reason=review.get("reason")
                        )
                        # El log solo no alcanza: el operador recibe igual la
                        # peticion de aprobacion y el bloque generico se parece
                        # bastante a un cierre normal, asi que un fallo de la
                        # capa LLM puede repetirse semanas sin que nadie lo
                        # note. Es lo que paso con el reetiquetado que encontro
                        # el contract test (ver ADR-001). El aviso va antes de
                        # pedir aprobacion para que llegue en ese orden.
                        self.telegram.send_alert(
                            "⚠️ El validador rechazó el titular generado; se "
                            "publica el bloque genérico.\n\n"
                            f"Motivo: {review.get('reason')}\n\n"
                            f"Titular descartado: {headline}"
                        )
                        headline = (
                            f"📊 Cierre de Mercado Semanal:\n"
                            f"S&P500: {data.sp500_weekly_return * 100:+.2f}%\n"
                            f"NASDAQ: {data.nasdaq_weekly_return * 100:+.2f}%"
                        )

                # ── FASE HITL ──────────────────────────────────────────────────
                with (
                    self.tracer.start_as_current_span("hitl_telegram")
                    if self.tracer
                    else nullcontext()
                ):
                    logger.info("requesting_human_approval")
                    msg_id = self.telegram.send_approval_request(
                        text=headline, image_bytes=image_bytes
                    )

                    try:
                        approved = self.telegram.wait_for_approval(
                            msg_id, timeout_seconds=3600
                        )
                    except TelegramBotError:
                        self.state.mark_expired(event_id)
                        logger.error("pipeline_expired_telegram_timeout")
                        return

                # ── FASE DE PUBLICACIÓN ────────────────────────────────────────
                if approved:
                    logger.info("pipeline_approved_publishing")

                    image_url = None
                    if self.r2_ready:
                        image_url = self.r2.upload_image(image_bytes, f"{event_id}.png")

                    if self.publishers_ready:
                        with (
                            self.tracer.start_as_current_span("publish")
                            if self.tracer
                            else nullcontext()
                        ):
                            # X: publicar solo si no se hizo ya
                            if not x_already_done:
                                x_result = self.x_client.post_tweet(headline)
                                x_post_id = x_result.get("data", {}).get(
                                    "id", "unknown"
                                )
                                self.state.mark_x_published(event_id, x_post_id)
                            else:
                                logger.info(
                                    "x_already_published_skipping", event_id=event_id
                                )

                            # LinkedIn: publicar solo si no se hizo ya
                            if not linkedin_already_done:
                                li_result = self.linkedin.post_text(headline)
                                li_post_id = li_result.get("id", "unknown")
                                self.state.mark_linkedin_published(event_id, li_post_id)
                            else:
                                logger.info(
                                    "linkedin_already_published_skipping",
                                    event_id=event_id,
                                )

                    # Marcar como completamente publicado con todos los metadatos
                    self.state.mark_as_published(
                        event_id,
                        image_url=image_url,
                        data_source=data_source,
                        sp500_close=data.sp500_close,
                        nasdaq_close=data.nasdaq_close,
                        sp500_return=data.sp500_weekly_return,
                        nasdaq_return=data.nasdaq_weekly_return,
                        prompt_version=_PROMPT_VERSION,
                        headline=headline,
                        validator_approved=validator_approved,
                        macro=data.macro,
                    )
                    logger.info("pipeline_completed_successfully")
                else:
                    self.state.mark_failed(event_id, reason="rejected_by_human")
                    logger.warning("pipeline_aborted_by_human")

            except Exception as e:
                logger.error("pipeline_failed_critically", error=str(e))
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
