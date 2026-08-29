"""El orquestador tiene que llevar el snapshot macro hasta StateDB.

Construir un `MacroOrchestrator` real requiere credenciales de siete servicios,
así que se instancia sin `__init__` y se le inyectan colaboradores falsos. Lo
que se verifica no es la lógica de esos colaboradores (ya tienen sus unit
tests) sino el cableado: que lo que se calculó en la fase de datos termine
guardado al cerrar la run.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.llm.client import FALLBACK_HEADLINE
from macro_pipeline.llm.validator import (
    API_ERROR_REASON_PREFIX,
    TOOL_FAILURE_REASON,
)
from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.storage.r2_client import R2ClientError
from macro_pipeline.validators.schemas import MacroSnapshot, WeeklyCloseData


@pytest.fixture
def snapshot():
    return MacroSnapshot(
        cpi_yoy=0.033,
        cpi_as_of=date(2026, 7, 1),
        unemployment_rate=4.1,
        unrate_as_of=date(2026, 7, 1),
        treasury_10y=4.65,
        dgs10_as_of=date(2026, 8, 20),
    )


def _build_orchestrator(data: WeeklyCloseData) -> MacroOrchestrator:
    """Orquestador con todos los colaboradores externos mockeados."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.tracer = None
    orch.r2 = None
    orch._allow_mock = False

    # Con publicadores: el camino normal. `x_ready` / `linkedin_ready` /
    # `r2_ready` derivan del cliente y `x_error` / `linkedin_error` de
    # `component_errors`, y todas son de solo lectura: declarar una red lista
    # obliga a poner un cliente, y declararla rota obliga a escribir el motivo
    # donde lo escribiria el constructor. Es a proposito: el atajo de setear la
    # bandera a mano es como el bug de `5ba7997` estuvo escondido detras de
    # cuatro tests verdes.
    orch.switch_errors = {}
    orch.component_errors = {}
    orch.macro_error = None
    orch.x_client = MagicMock()
    orch.x_client.post_tweet.return_value = {"data": {"id": "x-123"}}
    orch.linkedin = MagicMock()
    orch.linkedin.post_text.return_value = {"id": "li-456"}

    orch.state = MagicMock()
    orch.state.is_published.return_value = False
    orch.state.is_in_progress.return_value = False
    orch.state.get_publication_state.return_value = {}

    orch.validator_engine = MagicMock()
    orch.renderer = MagicMock()
    orch.renderer.render_weekly_close.return_value = b"fake-png"

    orch.llm = MagicMock()
    orch.llm.generate_headline.return_value = "Titular de prueba"
    orch.validator_agent = MagicMock()
    orch.validator_agent.review_draft.return_value = {"approved": True}

    orch.telegram = MagicMock()
    orch.telegram.send_approval_request.return_value = 42
    orch.telegram.wait_for_approval.return_value = True

    orch._fetch_weekly_close = MagicMock(return_value=(data, "fmp"))
    return orch


def test_macro_snapshot_reaches_state_db(snapshot):
    """El snapshot que viajó al LLM es el que se persiste al cerrar la run."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _build_orchestrator(data)

    orch.run_weekly_close()

    orch.state.mark_as_published.assert_called_once()
    assert orch.state.mark_as_published.call_args.kwargs["macro"] == snapshot


def test_run_without_macro_persists_none(snapshot):
    """Sin contexto macro la run se cierra igual, con `macro=None`."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=None,
    )
    orch = _build_orchestrator(data)

    orch.run_weekly_close()

    orch.state.mark_as_published.assert_called_once()
    assert orch.state.mark_as_published.call_args.kwargs["macro"] is None


def test_validator_rejection_alerts_the_operator(snapshot):
    """Un rechazo del validador tiene que avisar, no solo quedar en el log.

    Cuando el validador rechaza, el pipeline sustituye el titular por el bloque
    generico y sigue: el operador recibe la peticion de aprobacion habitual y
    nada le dice que la capa LLM fallo. Es el modo de fallo que encontramos el
    2026-08-24 (el generador etiquetaba el desempleo como IPC), y podia repetirse
    semanas sin que nadie se enterara.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _build_orchestrator(data)
    orch.validator_agent.review_draft.return_value = {
        "approved": False,
        "reason": "El borrador cita un 4,2% que en la fuente es el desempleo.",
    }

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_called_once()
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "desempleo" in aviso, "el aviso tiene que llevar el motivo del rechazo"
    assert "Titular de prueba" in aviso, "y el titular que se descarto"


def test_a_run_without_publishers_is_not_marked_as_published(snapshot):
    """Sin clientes de publicacion, el evento NO puede quedar como publicado.

    `publishers_ready` era False cuando faltaba una credencial de X o LinkedIn.
    Hasta el 2026-08-25 el bloque de publicacion estaba dentro de ese `if`
    pero `mark_as_published` quedaba fuera, al mismo nivel: el pipeline
    renderizaba, pedia aprobacion, el humano aprobaba, no se publicaba nada
    en ninguna red, y el evento quedaba marcado como publicado con headline y
    metadatos completos. La run siguiente veia `is_published() == True` y lo
    salteaba, asi que el cierre de esa semana no salia nunca.

    Lo unico que lo decia era un `logger.warning` al arrancar.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _build_orchestrator(data)
    orch.x_client = None
    orch.component_errors["x"] = "Faltan credenciales de X API."
    orch.linkedin = None
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    orch.state.mark_as_published.assert_not_called()


def test_a_run_without_publishers_does_not_bother_the_operator(snapshot):
    """Y no le pide aprobacion a un humano para algo que no puede publicar.

    Pedirla es peor que inutil: el operador aprueba, no pasa nada, y la unica
    senial de que el pipeline estaba roto es que el post nunca aparece. El
    aviso reemplaza a la peticion de aprobacion, no se suma a ella.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _build_orchestrator(data)
    orch.x_client = None
    orch.component_errors["x"] = "Faltan credenciales de X API."
    orch.linkedin = None
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    orch.telegram.send_approval_request.assert_not_called()
    orch.telegram.send_alert.assert_called_once()
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "credencial" in aviso.lower() or "publicaci" in aviso.lower()


def test_no_alert_when_the_validator_approves(snapshot):
    """Sin rechazo no hay aviso: un aviso por run seria ruido y se ignoraria."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _build_orchestrator(data)

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_not_called()


# ── R2 es opcional tambien cuando esta configurado (ADR-009, divergencia 2) ──


def _with_failing_r2(orch: MacroOrchestrator, error: Exception) -> MacroOrchestrator:
    orch.r2 = MagicMock()
    orch.r2.upload_image.side_effect = error
    return orch


@pytest.mark.parametrize(
    "error",
    [
        R2ClientError("Error subiendo a R2: AccessDenied"),
        # `upload_image` solo convierte `ClientError` en `R2ClientError`; un
        # corte de red llega como `EndpointConnectionError` de botocore y sale
        # crudo. Atrapar solo `R2ClientError` dejaria abierto justo el fallo
        # mas probable.
        ConnectionError("Could not connect to the endpoint URL"),
    ],
    ids=["r2_client_error", "red_caida"],
)
def test_a_failing_r2_upload_does_not_stop_the_publication(snapshot, error):
    """R2 caido degrada, no aborta — y menos despues de la aprobacion humana.

    ADR-007 declara R2 opcional ("el pipeline funciona sin R2, solo sin
    snapshots remotos") y el codigo lo cumplia solo cuando R2 no estaba
    configurado: configurado y fallando mataba la run con el humano ya habiendo
    aprobado y sin nada publicado en ninguna red. Era la politica al reves.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _with_failing_r2(_build_orchestrator(data), error)

    orch.run_weekly_close()

    orch.x_client.post_tweet.assert_called_once()
    orch.linkedin.post_text.assert_called_once()
    orch.state.mark_as_published.assert_called_once()
    assert orch.state.mark_as_published.call_args.kwargs["image_url"] is None


def test_a_failing_r2_upload_alerts_the_operator(snapshot):
    """Toda degradacion alerta: si no, el snapshot deja de subirse en silencio.

    Nada en el post publicado cambia cuando R2 falla, asi que sin aviso la
    unica senial seria que el bucket se queda vacio, y a eso no lo mira nadie.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )
    orch = _with_failing_r2(_build_orchestrator(data), R2ClientError("AccessDenied"))

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_called_once()
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "R2" in aviso
    assert "AccessDenied" in aviso, "el aviso tiene que llevar la causa real"


# ── La alerta tiene que decir que fallo (ADR-009, divergencia 1) ─────────────


def _data(snapshot) -> WeeklyCloseData:
    return WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=snapshot,
    )


@pytest.mark.parametrize(
    "reason",
    [
        f"{API_ERROR_REASON_PREFIX} Connection error.",
        TOOL_FAILURE_REASON,
    ],
    ids=["api_caida", "no_uso_la_tool"],
)
def test_the_alert_does_not_blame_the_validator_when_it_never_reviewed(
    snapshot, reason
):
    """Un validador que no pudo revisar no es un validador que rechazo.

    El `except` del agente devuelve `approved=False` igual que un rechazo real
    (por eso el control negativo del contract test existe), asi que la alerta
    decia "el validador rechazo el titular" tambien cuando lo que murio fue la
    API. La degradacion era correcta; el diagnostico mandaba al operador a
    revisar un prompt que no tenia nada malo.
    """
    orch = _build_orchestrator(_data(snapshot))
    orch.validator_agent.review_draft.return_value = {
        "approved": False,
        "reason": reason,
    }

    orch.run_weekly_close()

    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "rechaz" not in aviso.lower(), "no hubo rechazo: hubo un fallo tecnico"
    assert "Anthropic" in aviso


def test_a_real_rejection_still_names_the_validator(snapshot):
    """Y el rechazo de verdad sigue diciendose rechazo: el operador decide
    distinto segun cual de los dos sea."""
    orch = _build_orchestrator(_data(snapshot))
    orch.validator_agent.review_draft.return_value = {
        "approved": False,
        "reason": "El borrador cita un 4,2% que en la fuente es el desempleo.",
    }

    orch.run_weekly_close()

    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "rechaz" in aviso.lower()
    assert "desempleo" in aviso


def test_a_dead_generator_alerts_even_if_the_validator_approves(snapshot):
    """El fallo mas silencioso de los tres: nadie avisaba.

    Con la API caida el generador devuelve `FALLBACK_HEADLINE`, un titular sin
    ninguna cifra. El validador —que revisa que no haya cifras inventadas— lo
    aprueba sin problema, asi que no habia rechazo, no habia alerta, y el
    pipeline publicaba "Cierre Semanal: Resumen del Mercado" a secas. Es
    exactamente la degradacion invisible que ADR-009 existe para evitar.
    """
    orch = _build_orchestrator(_data(snapshot))
    orch.llm.generate_headline.return_value = FALLBACK_HEADLINE
    orch.validator_agent.review_draft.return_value = {"approved": True}

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_called_once()
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "Anthropic" in aviso
    assert "rechaz" not in aviso.lower()


def test_a_dead_generator_publishes_the_generic_block_with_the_real_figures(snapshot):
    """Y se publica el bloque generico, que si lleva las cifras.

    ADR-009 acepta que la API caida degrade porque "lo que se pierde es
    redaccion, no informacion" — las cifras las pone el pipeline. Publicar
    `FALLBACK_HEADLINE` tal cual perdia tambien la informacion.
    """
    orch = _build_orchestrator(_data(snapshot))
    orch.llm.generate_headline.return_value = FALLBACK_HEADLINE

    orch.run_weekly_close()

    publicado = orch.x_client.post_tweet.call_args[0][0]
    assert "+1.20%" in publicado
    assert "+1.90%" in publicado
    assert publicado == orch.linkedin.post_text.call_args[0][0]
