"""Ninguna salida del orquestador puede dejar la fila trabada en `in_progress`.

ADR-009 fija que un abort termina de una de dos formas —sin fila, o con un
estado terminal (`failed` / `expired`)— y que la tercera, quedar trabado, no se
acepta: el reintento del mismo `event_id` se salta en silencio en el guard de
`main.py` y ese cierre no sale nunca.

A diferencia de `test_orchestrator_persistence.py`, aca el `StateDB` es real y
sobre un fichero temporal. Es lo unico que permite verificar la afirmacion que
mas importa —que un reintento tras una publicacion a medias solo publica el
canal que falto— porque esa depende de lo que quedo *escrito* entre las dos
runs, no de con que argumentos se llamo a un mock.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.storage.state import StateDB
from macro_pipeline.telegram.bot import TelegramBotError
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


def _build_orchestrator(data: WeeklyCloseData, state: StateDB) -> MacroOrchestrator:
    """Orquestador con estado real y todo lo demas mockeado."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.tracer = None
    orch.r2_ready = False
    orch._allow_mock = False

    orch.x_error = None
    orch.linkedin_error = None
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


def test_a_failure_in_the_data_phase_leaves_the_row_failed(data, state):
    """El lock se toma antes de la fase de datos, asi que este era el caso peor.

    Alpha Vantage caida, Mock Data bloqueado y datos insuficientes para el
    retorno salen los tres por excepcion desde la misma linea, y hasta hoy los
    tres dejaban la fila trabada para siempre.
    """
    orch = _build_orchestrator(data, state)
    orch._fetch_weekly_close.side_effect = RuntimeError("todas las fuentes fallaron")

    with pytest.raises(RuntimeError):
        orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "failed"
    assert not state.is_in_progress(EVENT_ID)


def test_a_failure_while_publishing_keeps_the_post_id_of_what_did_go_out(data, state):
    """Si X publico y LinkedIn revento, el `post_id` de X tiene que sobrevivir.

    Es la mitad que hace segura la marca `failed`: sin el `post_id` persistido,
    el reintento republicaria en X un cierre que ya salio.
    """
    orch = _build_orchestrator(data, state)
    orch.linkedin.post_text.side_effect = RuntimeError("LinkedIn 503")

    with pytest.raises(RuntimeError):
        orch.run_weekly_close()

    row = state.get_publication_state(EVENT_ID)
    assert row["status"] == "failed"
    assert row["x_post_id"] == "x-123"
    assert row["linkedin_post_id"] is None


def test_the_retry_after_a_partial_publication_only_publishes_what_is_missing(
    data, state
):
    """La idempotencia parcial del docstring, ejercitada de punta a punta.

    Era codigo muerto: la fila quedaba en `in_progress` y la segunda run moria
    en el guard de duplicados antes de leer los `post_id`. Con el estado
    terminal y el lock re-armandose, el reintento salta X y publica solo
    LinkedIn.
    """
    primera = _build_orchestrator(data, state)
    primera.linkedin.post_text.side_effect = RuntimeError("LinkedIn 503")
    with pytest.raises(RuntimeError):
        primera.run_weekly_close()

    segunda = _build_orchestrator(data, state)

    segunda.run_weekly_close()

    segunda.x_client.post_tweet.assert_not_called()
    segunda.linkedin.post_text.assert_called_once()
    row = state.get_publication_state(EVENT_ID)
    assert row["status"] == "published"
    assert row["x_post_id"] == "x-123"
    assert row["linkedin_post_id"] == "li-456"


def test_a_human_rejection_leaves_the_row_failed(data, state):
    orch = _build_orchestrator(data, state)
    orch.telegram.wait_for_approval.return_value = False

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "failed"


def test_a_telegram_timeout_leaves_the_row_expired(data, state):
    orch = _build_orchestrator(data, state)
    orch.telegram.wait_for_approval.side_effect = TelegramBotError("timeout")

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "expired"


def test_a_run_with_both_publishers_broken_leaves_no_row_at_all(data, state):
    """El abort anterior al lock: sin fila, la proxima run reintenta sola."""
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.x_error = "Faltan credenciales de X API."
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}
    orch.telegram.send_alert.assert_called_once()


def test_a_run_with_both_publishers_disabled_says_nothing(data, state):
    """Las dos apagadas a proposito: sin fila y sin alerta.

    Avisarte de tu propia decision cada semana es el ruido que hace que se
    dejen de leer las alertas, y entonces la que importa se pierde.
    """
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.linkedin = None

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}
    orch.telegram.send_alert.assert_not_called()
    orch.telegram.send_approval_request.assert_not_called()


def test_one_disabled_and_one_broken_alerts_only_about_the_broken_one(data, state):
    """No publica nadie, asi que aborta; pero solo una de las dos es un fallo."""
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "LinkedIn" in aviso
    assert "X:" not in aviso, "una red apagada no se menciona como fallo"


def test_an_expired_run_can_be_retried(data, state):
    """Tras un timeout de Telegram, la run siguiente vuelve a tomar el lock.

    `mark_expired` existia desde el principio, pero `mark_in_progress` era
    `INSERT OR IGNORE`: el reintento encontraba la fila `expired`, no re-armaba
    nada y corria sin lock.
    """
    primera = _build_orchestrator(data, state)
    primera.telegram.wait_for_approval.side_effect = TelegramBotError("timeout")
    primera.run_weekly_close()

    segunda = _build_orchestrator(data, state)
    segunda.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"


def test_a_broken_linkedin_still_publishes_on_x(data, state):
    """Una red rota degrada: publica en la que si esta y el cierre sale.

    ADR-009: se degrada cuando el fallo solo cuesta contexto. Que falte la
    credencial de LinkedIn no hace que ninguna cifra sea incorrecta y no impide
    publicar — impide publicar en una red.
    """
    orch = _build_orchestrator(data, state)
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    fila = state.get_publication_state(EVENT_ID)
    assert fila["status"] == "published"
    assert fila["x_post_id"] == "x-123"
    assert not fila["linkedin_post_id"]
    orch.x_client.post_tweet.assert_called_once()


def test_the_degraded_run_warns_before_asking_for_approval(data, state):
    """Quien aprueba tiene que saber que ese cierre sale en una sola red.

    Mismo orden y mismo motivo que el aviso de la capa LLM.
    """
    orch = _build_orchestrator(data, state)
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    llamadas = [c[0] for c in orch.telegram.mock_calls]
    # Por contenido y no por posicion: el primer `send_alert` es el del
    # publicador solo porque el fixture aprueba el titular y deja `r2_ready`
    # en False. Un cambio ahi haria que esto empezara a medir otra alerta sin
    # avisar.
    idx_aviso = next(
        i
        for i, c in enumerate(orch.telegram.mock_calls)
        if c[0] == "send_alert" and "sale solo en" in c[1][0]
    )
    assert idx_aviso < llamadas.index("send_approval_request")
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "sale solo en X" in aviso
    assert "el cliente de LinkedIn no se pudo construir" in aviso
    assert "Faltan credenciales de LinkedIn." in aviso


def test_a_broken_x_still_publishes_on_linkedin(data, state):
    """El espejo del test de arriba, y no es redundante: sin el, la rama
    `x_error` de los dos ternarios de la alerta no la ejercita nadie y
    invertirlos deja la suite entera en verde.
    """
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.x_error = "Faltan credenciales de X API."

    orch.run_weekly_close()

    fila = state.get_publication_state(EVENT_ID)
    assert fila["status"] == "published"
    assert fila["linkedin_post_id"] == "li-456"
    assert not fila["x_post_id"]
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "sale solo en LinkedIn" in aviso
    assert "el cliente de X no se pudo construir" in aviso


def test_a_disabled_network_never_warns(data, state):
    """Apagada a proposito con la otra viva: publica y no dice nada.

    Es el caso del token de LinkedIn venciendo en octubre: con la bandera en
    false la run es verde y silenciosa en vez de degradada con alerta.
    """
    orch = _build_orchestrator(data, state)
    orch.linkedin = None

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"
    # `assert_not_called` a secas y no un filtro por texto: vale porque el
    # fixture aprueba el titular y deja `r2_ready` en False, asi que la unica
    # alerta posible en esta run seria la del publicador. Si eso cambia en el
    # fixture, este test empieza a fallar — que es la direccion correcta.
    orch.telegram.send_alert.assert_not_called()
