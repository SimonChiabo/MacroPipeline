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
    orch.r2_ready = False
    orch._allow_mock = False

    # Con publicadores: el camino normal. Antes esto era False, que era un
    # atajo para no mockear los dos clientes —no una afirmacion de que la run
    # deba llegar hasta el final sin publicadores—, y de paso dejaba los
    # cuatro tests de aca corriendo por el camino roto: sin publicar nada y
    # marcando el evento como publicado igual.
    orch.publishers_ready = True
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

    `publishers_ready` es False cuando falta una credencial de X o LinkedIn.
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
    orch.publishers_ready = False

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
    orch.publishers_ready = False

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
    orch.r2_ready = True
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
