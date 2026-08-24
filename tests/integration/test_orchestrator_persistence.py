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
    orch.publishers_ready = False
    orch._allow_mock = False

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
