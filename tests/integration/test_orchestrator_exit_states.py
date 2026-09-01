"""Ninguna salida del orquestador puede dejar la fila trabada en `in_progress`.

ADR-009 fija cuatro formas de terminar sin publicar, y la cuarta —quedar
trabado— es la que no se acepta: el reintento del mismo `event_id` se salta en
el guard de `main.py` y ese cierre no sale nunca. El salto sigue ocurriendo,
pero desde el aviso de lock viejo ya no es silencioso.

A diferencia de `test_orchestrator_persistence.py`, aca el `StateDB` es real y
sobre un fichero temporal. Es lo unico que permite verificar la afirmacion que
mas importa —que un reintento tras una publicacion a medias solo publica el
canal que falto— porque esa depende de lo que quedo *escrito* entre las dos
runs, no de con que argumentos se llamo a un mock.
"""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from macro_pipeline.llm.client import FALLBACK_HEADLINE
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
    orch.r2 = None
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


def _trabar_el_lock(state: StateDB, locked_at: str | None) -> None:
    """Deja la fila del evento en `in_progress` con el `locked_at` que se pida.

    Escribe la columna a mano porque lo que se está probando es qué hace el
    orquestador con una fila que ya estaba trabada cuando arrancó, y no hay
    forma de envejecer un lock esperando dos horas.
    """
    state.mark_in_progress(EVENT_ID)
    with sqlite3.connect(state.db_path) as conn:
        conn.execute(
            "UPDATE published_events SET locked_at = ? WHERE event_id = ?",
            (locked_at, EVENT_ID),
        )


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


def test_a_critical_failure_alerts_before_marking_the_row(data, state):
    """Una run que muere se rompio, y toda rotura avisa.

    Hasta hoy el unico abort que alertaba era el pre-lock de publicadores: los
    demas dejaban una fila `failed` que nadie mira.
    """
    orch = _build_orchestrator(data, state)
    orch._fetch_weekly_close.side_effect = RuntimeError("todas las fuentes fallaron")

    with pytest.raises(RuntimeError):
        orch.run_weekly_close()

    orch.telegram.send_alert.assert_called_once()
    assert "todas las fuentes fallaron" in orch.telegram.send_alert.call_args[0][0]
    assert state.get_publication_state(EVENT_ID)["status"] == "failed"


def test_the_alert_goes_out_before_the_row_is_marked(data, state):
    """El «before» del nombre de arriba, que hasta ahora no lo afirmaba nadie.

    El orden importa cuando la excepcion **es** un fallo del `StateDB`:
    `mark_failed` levanta tambien y sustituye la causa original por un
    «During handling...». Avisando primero, el operador se entera igual del
    motivo de verdad.
    """
    orch = _build_orchestrator(data, state)
    orch._fetch_weekly_close.side_effect = RuntimeError("murio en datos")

    estados: list[str] = []
    orch.telegram.send_alert.side_effect = lambda *_: estados.append(
        state.get_publication_state(EVENT_ID)["status"]
    )

    with pytest.raises(RuntimeError):
        orch.run_weekly_close()

    assert estados == ["in_progress"]


def test_a_death_without_telegram_reraises_the_real_cause(data, state):
    """La guarda `is not None` del manejador, que no ejercitaba ningun test.

    Es alcanzable, y este es el camino exacto: si alguien reordena las ramas del
    punto de decision y deja pasar una run sin canal, la guarda de `run_weekly_close`
    levanta — y esa excepcion cae en el manejador con `self.telegram` en None.
    Sin el `is not None` seria un `AttributeError` levantado *dentro* del
    manejador de fallos, que sustituiria la causa real por un fallo del propio
    aviso: el operador se quedaria sin saber que fue lo que se rompio.

    Muere antes del lock, asi que no llega a haber fila — que es justo lo que el
    texto del aviso dice ahora en vez de prometer un `failed` que no existe.
    """
    orch = _build_orchestrator(data, state)
    orch.telegram = None
    orch._startup_exit_code = MagicMock(return_value=None)

    with pytest.raises(RuntimeError, match="punto de decisión"):
        orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}


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
    orch.component_errors["x"] = "Faltan credenciales de X API."
    orch.linkedin = None
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

    assert orch.run_weekly_close() == 1

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

    assert orch.run_weekly_close() == 0

    assert state.get_publication_state(EVENT_ID) == {}
    orch.telegram.send_alert.assert_not_called()
    orch.telegram.send_approval_request.assert_not_called()


def test_one_disabled_and_one_broken_alerts_only_about_the_broken_one(data, state):
    """No publica nadie, asi que aborta; pero solo una de las dos es un fallo."""
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.linkedin = None
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

    assert orch.run_weekly_close() == 1

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
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

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
    orch.component_errors["linkedin"] = "Faltan credenciales de LinkedIn."

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
    # El aviso se mudo al punto de decision, que compone una linea por
    # componente con su motivo y su consecuencia. Ya no dice «el cliente de
    # LinkedIn no se pudo construir»: ese bloque se borro porque su causa
    # siempre fue de arranque y dejarlo sonaria dos veces por lo mismo.
    assert "linkedin" in aviso
    assert "Faltan credenciales de LinkedIn." in aviso
    assert "el cierre sale solo en X" in aviso


def test_a_broken_x_still_publishes_on_linkedin(data, state):
    """El espejo del test de arriba, y no es redundante: sin el, la entrada
    `x` de `_CONSECUENCIA` no la ejercita nadie e intercambiarla con la de
    `linkedin` deja la suite entera en verde.
    """
    orch = _build_orchestrator(data, state)
    orch.x_client = None
    orch.component_errors["x"] = "Faltan credenciales de X API."

    orch.run_weekly_close()

    fila = state.get_publication_state(EVENT_ID)
    assert fila["status"] == "published"
    assert fila["linkedin_post_id"] == "li-456"
    assert not fila["x_post_id"]
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "x: Faltan credenciales de X API." in aviso
    assert "el cierre sale solo en LinkedIn" in aviso


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
    # fixture aprueba el titular, deja `r2_ready` en False y `macro_error` en
    # None, asi que la unica alerta posible en esta run seria la del
    # publicador. Si eso cambia en el fixture, este test empieza a fallar —
    # que es la direccion correcta.
    orch.telegram.send_alert.assert_not_called()


def test_a_broken_macro_block_still_publishes_and_warns(data, state):
    """El bloque macro degrada, pero deja de hacerlo en silencio.

    ADR-009 dice que toda degradacion tiene que alertar o se vuelve invisible y
    se repite semanas. Este camino no lo cumplia.
    """
    orch = _build_orchestrator(data, state)
    orch.macro_error = "FREDClientError: FRED caído"

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "macro" in aviso.lower()
    assert "FREDClientError: FRED caído" in aviso, "tiene que llevar la causa real"


def test_the_macro_warning_arrives_before_the_approval_request(data, state):
    """Quien aprueba tiene que saber que ese cierre sale sin bloque macro."""
    orch = _build_orchestrator(data, state)
    orch.macro_error = "FREDClientError: FRED caído"

    orch.run_weekly_close()

    llamadas = [c[0] for c in orch.telegram.mock_calls]
    idx_aviso = next(
        i
        for i, c in enumerate(orch.telegram.mock_calls)
        if c[0] == "send_alert" and "macro" in c[1][0].lower()
    )
    assert idx_aviso < llamadas.index("send_approval_request")


def test_the_macro_warning_carries_the_validator_cause_verbatim(data, state):
    """Dos causas distintas no pueden dar el mismo aviso.

    Con el texto de la causa fijo en vez de interpolado, este test falla — que
    es exactamente el agujero por el que la alerta de publicadores pudo mentir
    con 148 tests en verde.
    """
    orch = _build_orchestrator(data, state)
    orch.macro_error = "El validador rechazó la cifra macro: cpi_yoy=0.9 fuera de rango"

    orch.run_weekly_close()

    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "cpi_yoy=0.9 fuera de rango" in aviso
    assert "FREDClientError" not in aviso


def test_fred_without_a_key_publishes_and_says_nothing(data, state):
    """El opcional sin configurar no participa, y no participar no es degradar.

    `macro_error` en None es como llega FRED sin key: el fixture ya lo deja
    asi, y el punto del test es que ese camino no gaste una alerta.
    """
    orch = _build_orchestrator(data, state)

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"
    orch.telegram.send_alert.assert_not_called()


def test_a_run_without_the_llm_layer_publishes_the_generic_block(data, state):
    """Sin capa LLM el cierre sale igual: lo que se pierde es redaccion.

    Las cifras las pone el pipeline, no el modelo, asi que el titular generico
    lleva la informacion entera. Es la premisa con la que ADR-009 acepta
    degradar aca.
    """
    orch = _build_orchestrator(data, state)
    orch.llm = None
    orch.validator_agent = None

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"
    titular = orch.telegram.send_approval_request.call_args[1]["text"]
    assert "+1.20%" in titular
    assert "+1.90%" in titular


def test_a_run_without_the_llm_layer_says_nothing(data, state):
    """El assert que fija el tercer eje de ADR-009.

    Un opcional sin configurar no participa, y no participar no es degradar.
    Avisar todas las semanas de una configuracion permanente es el ruido que
    hace que se deje de leer el aviso que importa, y rompe la distincion que
    ADR-009 fija: si llega una alerta, es porque algo se rompio.

    `assert_not_called` a secas vale por lo mismo que en los tests de mas
    arriba: el fixture deja `r2_ready` en False, `macro_error` en None y las
    dos redes vivas, asi que la unica alerta posible en esta run seria la de
    la capa LLM.
    """
    orch = _build_orchestrator(data, state)
    orch.llm = None
    orch.validator_agent = None

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_not_called()


def test_a_run_without_the_llm_layer_records_no_prompt_and_no_verdict(data, state):
    """NULL significa "no ocurrio", igual que las seis columnas macro sin FRED.

    Escribir la version de prompt afirmaria una llamada que no se hizo. El
    criterio no es quien escribio el titular sino si **hubo llamada**: la run
    degradada-pero-configurada tambien publica texto del pipeline y si registra
    la version, porque el modelo respondio (ver
    `test_a_degraded_but_configured_run_still_records_the_prompt_version`).
    Aca no hubo ninguna. `validator_approved=False` se leeria como "el
    validador lo rechazo", que tampoco paso.
    """
    orch = _build_orchestrator(data, state)
    orch.llm = None
    orch.validator_agent = None

    orch.run_weekly_close()

    row = state.get_publication_state(EVENT_ID)
    assert row["prompt_version"] is None
    assert row["validator_approved"] is None


def test_a_normal_run_still_records_the_prompt_version(data, state):
    """La otra direccion: sin esto, poner las dos a None siempre pasaria."""
    orch = _build_orchestrator(data, state)

    orch.run_weekly_close()

    row = state.get_publication_state(EVENT_ID)
    assert row["prompt_version"] is not None
    assert "headline=" in row["prompt_version"]
    assert row["validator_approved"] == 1


@pytest.fixture
def data_sin_niveles():
    """Lo que devuelve la ruta de Alpha Vantage: retorno sí, nivel no."""
    return WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=None,
        sp500_weekly_return=0.012,
        nasdaq_close=None,
        nasdaq_weekly_return=0.019,
        macro=None,
    )


def test_sin_nivel_la_capa_llm_no_participa(data_sin_niveles, state):
    """Sin nivel no hay cifra que el LLM pueda re-rotular mal.

    Es ADR-001 sostenida por construcción: el `data_str` ni se arma.
    """
    orch = _build_orchestrator(data_sin_niveles, state)
    orch._fetch_weekly_close.return_value = (data_sin_niveles, "av")

    orch.run_weekly_close()

    orch.llm.generate_headline.assert_not_called()
    orch.validator_agent.review_draft.assert_not_called()


def test_sin_nivel_el_titular_es_el_generico(data_sin_niveles, state):
    orch = _build_orchestrator(data_sin_niveles, state)
    orch._fetch_weekly_close.return_value = (data_sin_niveles, "av")

    orch.run_weekly_close()

    texto = orch.x_client.post_tweet.call_args[0][0]
    assert "Cierre de Mercado Semanal" in texto
    assert "Titular de prueba" not in texto


def test_con_nivel_la_capa_llm_sigue_participando(data, state):
    """La ruta de FMP no cambia: el cambio es sobre el dato, no sobre el LLM."""
    orch = _build_orchestrator(data, state)

    orch.run_weekly_close()

    orch.llm.generate_headline.assert_called_once()


def test_a_degraded_but_configured_run_still_records_the_prompt_version(data, state):
    """El caso del medio, y el que fija donde vive `prompt_version`.

    Con la API caida el generador devuelve `FALLBACK_HEADLINE`: la run alerta y
    publica el mismo bloque generico que una run sin capa LLM. Lo que separa
    las dos filas no es quien redacto el titular —el pipeline, en las dos—
    sino que aca **si hubo llamada**, asi que la version queda escrita.

    Por eso `prompt_version = _PROMPT_VERSION` va dentro del `else` pero fuera
    del `with`: corre degrade o no degrade. Moverlo adentro de la rama sin
    degradacion dejaria esta fila en NULL —perdiendo la trazabilidad justo en
    las runs que mas hay que diagnosticar— y ningun otro test lo veria.
    """
    orch = _build_orchestrator(data, state)
    orch.llm.generate_headline.return_value = FALLBACK_HEADLINE

    orch.run_weekly_close()

    orch.telegram.send_alert.assert_called_once()
    row = state.get_publication_state(EVENT_ID)
    assert row["prompt_version"] is not None
    assert row["validator_approved"] == 1


def test_ruta_av_avisa_antes_de_pedir_aprobacion(state, data):
    """Quien aprueba tiene que saber que ese cierre sale con menos."""
    orch = _build_orchestrator(data, state)
    orch.fmp_runtime_error = "FMPClientError: 503"

    orch.run_weekly_close()

    alertas = [c[0][0] for c in orch.telegram.send_alert.call_args_list]
    assert any("Alpha Vantage" in a and "variación semanal" in a for a in alertas)


def test_fmp_sin_key_no_alerta_dos_veces_en_la_run(state, data):
    """El arranque ya avisó: `fmp_runtime_error` en None es como llega ese caso."""
    orch = _build_orchestrator(data, state)
    orch.fmp_runtime_error = None

    orch.run_weekly_close()

    alertas = [c[0][0] for c in orch.telegram.send_alert.call_args_list]
    assert not any("Alpha Vantage" in a for a in alertas)


def test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre(data, state):
    """La cuarta forma de abortar de ADR-009 deja de ser silenciosa.

    El `event_id` lleva la fecha, así que la fila trabada no bloquea la semana
    que viene: bloquea *este* cierre, y cada relanzamiento del mismo día se lo
    salta. Sin esto, la única señal de que faltó una publicación semanal es una
    línea de log en un runner efímero que nadie mira.
    """
    orch = _build_orchestrator(data, state)
    viejo = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    _trabar_el_lock(state, viejo)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()
    assert EVENT_ID in orch.telegram.send_alert.call_args[0][0]


def test_un_lock_sin_fecha_alerta(data, state):
    """`locked_at` en NULL es una fila anterior a la migración.

    Y no va a dejar de estarlo nunca: `mark_in_progress` no toca las filas
    `in_progress`. Callarse acá sería preservar el salto en silencio para
    siempre, justo en la fila que motiva todo este trabajo.
    """
    orch = _build_orchestrator(data, state)
    _trabar_el_lock(state, None)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()


def test_un_lock_reciente_no_alerta(data, state):
    """Veinte minutos de `in_progress` es una run sana esperando aprobación.

    `wait_for_approval` espera al humano hasta una hora entera, así que alertar
    acá convertiría cada aprobación pausada en una alerta falsa — y una alerta
    que a veces es ruido se aprende a ignorar.
    """
    orch = _build_orchestrator(data, state)
    reciente = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    _trabar_el_lock(state, reciente)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()


def test_un_lock_ilegible_avisa_sin_soltar_el_lock(data, state):
    """Un `locked_at` que no se puede leer no puede terminar soltando el lock.

    Es el caso que esta misma alerta provoca: existe para mandar a un humano a
    editar la fila a mano, y la base viaja por R2 entre runs efímeras, así que
    un valor escrito a mano es la entrada de diseño y no una rareza. Sin el
    `except`, la resta contra un valor naive explota, el manejador general
    marca `failed` — y el relanzamiento siguiente toma el lock y publica en
    paralelo con la aprobación que seguía viva.

    Por eso lo que ancla el test no es que alerte, sino que la fila siga
    `in_progress` después.
    """
    orch = _build_orchestrator(data, state)
    # El mismo instante, escrito sin offset: es lo que rompe la resta.
    ilegible = (datetime.now(UTC) - timedelta(hours=5)).replace(tzinfo=None).isoformat()
    _trabar_el_lock(state, ilegible)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_called_once()
    assert state.get_publication_state(EVENT_ID)["status"] == "in_progress"
