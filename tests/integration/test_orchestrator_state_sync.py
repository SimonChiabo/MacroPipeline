"""El sincronizado del estado, visto desde el orquestador.

Con `StateDB` real, como el resto de `tests/integration/`, porque la mitad de
lo que hay que afirmar es **que fila queda**: la diferencia entre un abort
pre-lock (sin fila, la proxima run reintenta sola) y uno que deja rastro.

Lo que mas se fija aca es el **orden**: el pull tiene que correr antes del
guard de duplicados —consultar `is_published` sobre una base que no se pudo
bajar responderia que no se publico nunca— y su rama del punto de decision
tiene que ir despues de la de Telegram, porque no se puede avisar de un pull
roto sin canal. Las dos son la forma exacta del punto 13.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.storage.state import StateDB
from macro_pipeline.storage.state_sync import StateSyncError
from macro_pipeline.validators.schemas import WeeklyCloseData

EVENT_ID = f"weekly_close_{date.today()}"


class _FakeSync:
    """Doble de StateSync. `pull` nunca levanta; `push` si, cuando toca."""

    def __init__(self, pull_error=None, remote_absent=False, push_falla_en=None):
        self.pull_error = pull_error
        self.remote_absent = remote_absent
        self._push_falla_en = push_falla_en
        self.pulls = 0
        self.pushes = 0

    def pull(self):
        self.pulls += 1
        return self.pull_error

    def push(self):
        self.pushes += 1
        if self._push_falla_en is not None and self.pushes == self._push_falla_en:
            raise StateSyncError("No se pudo subir el estado a R2: AccessDenied")


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


def _orchestrator(data: WeeklyCloseData, state: StateDB, sync) -> MacroOrchestrator:
    """Todo sano y todo mockeado; cada test rompe lo suyo."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.tracer = None
    orch._allow_mock = False
    orch.switch_errors = {}
    orch.component_errors = {}
    orch.macro_error = None
    orch.state_sync_error = None

    orch.fmp = MagicMock()
    orch.av = MagicMock()
    orch.fred = MagicMock()
    orch.r2 = MagicMock()
    orch.r2.upload_image.return_value = "r2://bucket/x.png"
    orch.x_client = MagicMock()
    orch.x_client.post_tweet.return_value = {"data": {"id": "x-123"}}
    orch.linkedin = MagicMock()
    orch.linkedin.post_text.return_value = {"id": "li-456"}

    orch.state = state
    orch.state_sync = sync
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


def _state(tmp_path, sync=None) -> StateDB:
    return StateDB(
        db_path=str(tmp_path / "state.db"),
        on_write=(sync.push if sync is not None else None),
    )


# ── El pull roto ─────────────────────────────────────────────────────────────


def test_un_pull_roto_aborta_antes_del_lock_y_no_deja_fila(tmp_path, data):
    """Sin estado confiable no se puede publicar: podria ser un duplicado.

    Es la rama del criterio de ADR-009 que aborta —el fallo no cuesta contexto,
    arriesga que salga dos veces el mismo cierre— y aborta **antes del lock**,
    asi que no queda fila y la proxima run reintenta sola.
    """
    sync = _FakeSync(pull_error="No se pudo bajar el estado de R2: timeout")
    state = _state(tmp_path, sync)
    orch = _orchestrator(data, state, sync)

    assert orch.run_weekly_close() == 1

    orch.telegram.send_alert.assert_called_once()
    assert "estado" in orch.telegram.send_alert.call_args[0][0].lower()
    assert state.get_publication_state(EVENT_ID) == {}


def test_un_pull_roto_no_consulta_el_guard_de_duplicados(tmp_path, data):
    """El orden importa: `is_published` sobre una base que no se bajo miente.

    Responderia que no se publico nunca, que es exactamente la respuesta que
    republica el cierre de la semana.
    """
    sync = _FakeSync(pull_error="No se pudo bajar el estado de R2: timeout")
    state = _state(tmp_path, sync)
    state.is_published = MagicMock(return_value=False)
    orch = _orchestrator(data, state, sync)

    orch.run_weekly_close()

    state.is_published.assert_not_called()


def test_el_pull_corre_antes_que_el_guard(tmp_path, data):
    """Con el estado sano, el pull ya ocurrio cuando se consulta el guard."""
    sync = _FakeSync()
    state = _state(tmp_path, sync)
    orden: list[str] = []
    real = state.is_published
    state.is_published = lambda e: (orden.append("guard"), real(e))[1]
    sync_pull = sync.pull
    sync.pull = lambda: (orden.append("pull"), sync_pull())[1]
    orch = _orchestrator(data, state, sync)

    orch.run_weekly_close()

    assert orden[:2] == ["pull", "guard"]


# ── El remoto ausente ────────────────────────────────────────────────────────


def test_el_remoto_ausente_sigue_y_avisa(tmp_path, data):
    """Primera corrida o perdida: son indistinguibles, y hay que decirlo asi.

    No aborta —una primera corrida legitima tiene que poder publicar— pero
    tampoco calla, porque la otra mitad del caso es el estado perdido, que es
    justo lo que este trabajo existe para que no pase en silencio.
    """
    sync = _FakeSync(remote_absent=True)
    state = _state(tmp_path, sync)
    orch = _orchestrator(data, state, sync)

    assert orch.run_weekly_close() == 0

    avisos = [c[0][0] for c in orch.telegram.send_alert.call_args_list]
    assert any("primera corrida" in a.lower() for a in avisos)
    assert state.get_publication_state(EVENT_ID)["status"] == "published"


# ── El push roto ─────────────────────────────────────────────────────────────


def test_un_push_roto_cierra_la_fila_como_failed(tmp_path, data):
    """El primer push es el de `mark_in_progress`, antes de publicar nada.

    Levanta, lo atrapa el manejador general y la fila queda `failed`. Nada
    salio a ninguna red, asi que no hay riesgo de duplicado: la proxima run
    re-arma el lock sobre `failed` y reintenta limpio.
    """
    sync = _FakeSync(push_falla_en=1)
    state = _state(tmp_path, sync)
    orch = _orchestrator(data, state, sync)

    # El manejador general re-levanta a proposito (`raise` al final del
    # `except`): quien traduce a codigo de salida es el `sys.exit` del
    # `__main__`. Lo que se afirma aca es el estado que deja, que es lo que
    # lee la corrida siguiente.
    with pytest.raises(StateSyncError):
        orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "failed"


def test_el_push_de_mark_failed_no_vuelve_a_levantar(tmp_path, data):
    """La no-recursion: el manejador de fallos no puede reventar el mismo.

    Con el push roto para siempre, `mark_in_progress` levanta y el `except`
    general llama a `mark_failed`, cuyo propio push tambien falla. Si ese
    segundo fallo propagara, saldria del manejador y taparia la causa original.
    """
    sync = _FakeSync()
    sync._push_falla_en = None
    state = _state(tmp_path, sync)

    def siempre_falla():
        sync.pushes += 1
        raise StateSyncError("R2 caido")

    sync.push = siempre_falla
    state._on_write = siempre_falla
    orch = _orchestrator(data, state, sync)

    # Lo que se afirma es que sale **el fallo original** y que la fila igual
    # quedo cerrada. Si el push de `mark_failed` propagara, la excepcion
    # saldria desde dentro del manejador y la fila se quedaria `in_progress`
    # para siempre — la forma que ADR-009 no acepta.
    with pytest.raises(StateSyncError):
        orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "failed"


# ── El orden de las ramas ────────────────────────────────────────────────────


def test_sin_telegram_gana_la_rama_de_telegram(tmp_path, data):
    """**El orden de estas dos ramas es obligatorio, no estetico.**

    Con el pull roto y sin canal a la vez, si la rama del estado fuera primero
    intentaria alertar sobre un `None` y reventaria dentro del punto de
    decision. No se puede avisar de un pull roto sin canal: primero se
    establece que hay canal, despues se usa.

    Si alguien reordena esas dos ramas, este test es lo que lo caza.
    """
    sync = _FakeSync(pull_error="No se pudo bajar el estado de R2: timeout")
    state = _state(tmp_path, sync)
    orch = _orchestrator(data, state, sync)
    orch.telegram = None
    orch.component_errors["telegram"] = "Falta TELEGRAM_BOT_TOKEN."

    assert orch.run_weekly_close() == 1
    assert state.get_publication_state(EVENT_ID) == {}


# ── Sin R2 ───────────────────────────────────────────────────────────────────


def test_sin_r2_no_sincroniza_y_todo_sigue_igual(tmp_path, data):
    """R2 sin configurar no participa: el pipeline corre contra disco local."""
    state = _state(tmp_path, None)
    orch = _orchestrator(data, state, None)
    orch.r2 = None

    assert orch.run_weekly_close() == 0
    assert state.get_publication_state(EVENT_ID)["status"] == "published"


def test_una_corrida_que_aborta_a_proposito_no_avisa_del_remoto_ausente(tmp_path, data):
    """El aviso solo se debe en las corridas que llegan a publicar.

    Salio de recorrer la tabla de ADR-009 contra el codigo: el aviso estaba
    antes de las ramas de aborto, asi que una corrida apagada a proposito —FMP
    desactivado, que la tabla describe como «aborta en silencio»— igual mandaba
    un Telegram. Un cierre que no publica no puede duplicar nada, asi que el
    estado perdido no le cuesta nada a *esa* corrida, y avisar seria ruido
    sobre una decision propia.

    Si alguien vuelve a subir ese bloque por delante de los abortos, esto lo
    caza.
    """
    sync = _FakeSync(remote_absent=True)
    state = _state(tmp_path, sync)
    orch = _orchestrator(data, state, sync)
    orch.fmp = None  # apagado a proposito: no hay motivo en component_errors

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()
    assert state.get_publication_state(EVENT_ID) == {}
