"""El punto de decision: lo que el constructor dejo roto se reporta una vez.

Con `StateDB` real, como `test_orchestrator_exit_states.py`, porque la mitad de
lo que hay que afirmar es **que no queda fila**: un abort pre-lock que dejara
una fila `in_progress` haria que la proxima run se saltara el cierre en
silencio, que es la forma que ADR-009 no acepta.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.components import USE_TELEGRAM_VAR
from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.storage.state import StateDB
from macro_pipeline.validators.schemas import WeeklyCloseData

EVENT_ID = f"weekly_close_{date.today()}"


@pytest.fixture
def state(tmp_path):
    return StateDB(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def data():
    return WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
        macro=None,
    )


def _orchestrator(data: WeeklyCloseData, state: StateDB) -> MacroOrchestrator:
    """Todo sano y todo mockeado; cada test rompe lo suyo."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.tracer = None
    orch._allow_mock = False
    orch.switch_errors = {}
    orch.component_errors = {}
    orch.macro_error = None
    orch.fmp_runtime_error = None
    # Sin R2 no hay sincronizado: el pipeline corre contra el disco local,
    # que es como corrian estos tests antes de que el estado viajara.
    orch.state_sync = None
    orch.state_sync_error = None

    orch.fmp = MagicMock()
    orch.av = MagicMock()
    orch.fred = MagicMock()
    orch.r2 = None
    orch.x_client = MagicMock()
    orch.x_client.post_tweet.return_value = {"data": {"id": "x-123"}}
    orch.linkedin = MagicMock()
    orch.linkedin.post_text.return_value = {"id": "li-456"}

    orch.state = state
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


def test_a_broken_fmp_degrades_to_alpha_vantage_and_publishes(data, state):
    """FMP sin key ya no aborta: degrada a la ruta de AV (ADR-009, divergencia 4).

    Esa ruta ahora publica el retorno sin el nivel, asi que la run sigue.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors["fmp"] = "Se requiere FMP_API_KEY en el entorno."

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()
    texto = orch.telegram.send_alert.call_args[0][0]
    assert "FMP_API_KEY" in texto
    assert "Alpha Vantage" in texto
    assert state.get_publication_state(EVENT_ID) != {}


def test_fmp_switched_off_aborts_in_silence(data, state):
    """Apagar la unica fuente con ruta viva impide publicar: es un abort.

    Deliberado, asi que no alerta — y `0`, porque nadie se equivoco.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()
    assert state.get_publication_state(EVENT_ID) == {}


def test_fmp_sin_key_degrada_en_vez_de_abortar(data, state):
    """La consecuencia que ADR-009 dejó escrita de antemano."""
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {"fmp": "Se requiere FMP_API_KEY."}

    assert orch._startup_exit_code(EVENT_ID) is None


def test_fmp_sin_key_alerta_y_nombra_la_consecuencia(data, state):
    """La rama 5 indexa `_CONSECUENCIA[c]` directo: sin la clave, KeyError.

    Este test es el que impide que el aviso de la degradación se convierta en
    la excepción que mata la run.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {"fmp": "Se requiere FMP_API_KEY."}

    orch._startup_exit_code(EVENT_ID)

    alerta = orch.telegram.send_alert.call_args[0][0]
    assert "fmp" in alerta
    assert "Alpha Vantage" in alerta


def test_use_fmp_false_sigue_siendo_pausa_en_silencio(data, state):
    """Un switch apagado es una decisión, no un fallo: no se sustituye."""
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {}

    assert orch._startup_exit_code(EVENT_ID) == 0
    orch.telegram.send_alert.assert_not_called()


def test_a_broken_telegram_aborts_without_trying_to_alert(data, state):
    """El caso irreducible: no hay canal para avisar de que no hay canal."""
    orch = _orchestrator(data, state)
    telegram = orch.telegram
    orch.telegram = None
    orch.component_errors["telegram"] = "Faltan credenciales TELEGRAM_BOT_TOKEN."

    assert orch.run_weekly_close() == 1

    telegram.send_alert.assert_not_called()
    assert state.get_publication_state(EVENT_ID) == {}


def test_telegram_switched_off_pauses_the_pipeline_in_silence(data, state):
    orch = _orchestrator(data, state)
    orch.telegram = None

    assert orch.run_weekly_close() == 0
    assert state.get_publication_state(EVENT_ID) == {}


def test_an_invalid_telegram_switch_is_not_read_as_a_deliberate_pause(data, state):
    """El agujero que el orden de las ramas evita.

    `read_switch` devuelve `(False, motivo)` ante un valor invalido, asi que el
    cliente no se construye y queda **identico** a un apagado deliberado. Si la
    rama del apagado fuera primero, esto saldria con `0` y en silencio: el mismo
    caso invisible que este trabajo existe para cerrar.
    """
    orch = _orchestrator(data, state)
    orch.telegram = None
    orch.switch_errors[USE_TELEGRAM_VAR] = (
        "USE_TELEGRAM='maybe' no es un valor valido: se espera 'true' o 'false'."
    )

    assert orch.run_weekly_close() == 1
    assert state.get_publication_state(EVENT_ID) == {}


def test_an_invalid_switch_alerts_when_there_is_a_channel(data, state):
    orch = _orchestrator(data, state)
    orch.switch_errors["USE_FRED"] = (
        "USE_FRED='maybe' no es un valor valido: se espera 'true' o 'false'."
    )

    assert orch.run_weekly_close() == 1

    orch.telegram.send_alert.assert_called_once()
    assert "USE_FRED" in orch.telegram.send_alert.call_args[0][0]


def test_two_startup_degradations_produce_one_alert_naming_both(data, state):
    """Una alerta y no tres. El texto se compone; fijarlo rompe este test."""
    orch = _orchestrator(data, state)
    orch.fred = None
    orch.component_errors["fred"] = "Se requiere FRED_API_KEY en el entorno."
    orch.x_client = None
    orch.component_errors["x"] = "Faltan credenciales de X."

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()
    texto = orch.telegram.send_alert.call_args[0][0]
    assert "FRED_API_KEY" in texto
    assert "Faltan credenciales de X" in texto
    assert "bloque macro" in texto
    assert "solo en LinkedIn" in texto


def test_one_broken_publisher_alerts_once_and_not_twice(data, state):
    """El aviso de red caida se mudo al punto de decision, no se duplico.

    Su causa siempre fue de arranque —`x_error` se escribe en `__init__`—, asi
    que dejar tambien el bloque de `publisher_degraded` mandaria dos alertas por
    lo mismo y el operador aprenderia a ignorarlas.
    """
    orch = _orchestrator(data, state)
    orch.x_client = None
    orch.component_errors["x"] = "Faltan credenciales de X."

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()
    assert "solo en LinkedIn" in orch.telegram.send_alert.call_args[0][0]


def test_a_healthy_startup_does_not_alert(data, state):
    orch = _orchestrator(data, state)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()


def test_the_gate_runs_after_the_duplicate_guard(data, state):
    """Si ese cierre ya salio no hay nada que reportar, y alertar seria ruido."""
    orch = _orchestrator(data, state)
    orch.fred = None
    orch.component_errors["fred"] = "Se requiere FRED_API_KEY en el entorno."
    state.mark_in_progress(EVENT_ID)
    state.mark_as_published(EVENT_ID, data_source="fmp", headline="ya salio")

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()


def test_a_missing_telegram_after_the_gate_fails_loudly(data, state):
    """La guarda que protege a las cinco llamadas de abajo.

    Si alguien reordena las ramas y deja pasar una run sin canal, la run muere
    con un motivo legible en vez de con un `AttributeError` tres fases despues,
    con el humano ya esperando una aprobacion que nadie le pidio.
    """
    orch = _orchestrator(data, state)
    orch.telegram = None
    orch._startup_exit_code = MagicMock(return_value=None)

    with pytest.raises(RuntimeError, match="punto de decisión"):
        orch.run_weekly_close()
