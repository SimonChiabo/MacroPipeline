"""Tests de StateDB, con foco en la persistencia del contexto macro."""

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from macro_pipeline.storage.state import StateDB
from macro_pipeline.validators.schemas import MacroSnapshot

# Schema anterior a la migración macro: el que tienen las bases ya creadas.
_OLD_SCHEMA = """
    CREATE TABLE published_events (
        event_id            TEXT PRIMARY KEY,
        status              TEXT NOT NULL DEFAULT 'in_progress',
        created_at          TIMESTAMP NOT NULL,
        published_at        TIMESTAMP,
        data_source         TEXT,
        sp500_close         REAL,
        nasdaq_close        REAL,
        sp500_return        REAL,
        nasdaq_return       REAL,
        prompt_version      TEXT,
        headline            TEXT,
        validator_approved  INTEGER,
        image_url           TEXT,
        x_post_id           TEXT,
        linkedin_post_id    TEXT
    )
"""


@pytest.fixture
def db(tmp_path):
    """StateDB sobre un archivo temporal, aislado del estado real del usuario."""
    return StateDB(db_path=str(tmp_path / "state.db"))


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


def test_mark_as_published_persists_macro_snapshot(db, snapshot):
    """Los seis campos macro sobreviven al guardado y se recuperan igual."""
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", macro=snapshot)

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["cpi_yoy"] == pytest.approx(0.033)
    assert state["cpi_as_of"] == "2026-07-01"
    assert state["unemployment_rate"] == pytest.approx(4.1)
    assert state["unrate_as_of"] == "2026-07-01"
    assert state["treasury_10y"] == pytest.approx(4.65)
    assert state["dgs10_as_of"] == "2026-08-20"


def test_mark_as_published_without_macro_leaves_nulls(db):
    """Sin snapshot macro se guarda igual: es el camino habitual, no el borde.

    `safe_build_macro_snapshot` devuelve None cuando FRED falla, y eso no debe
    impedir que el cierre semanal quede registrado como publicado.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", headline="Cierre semanal")

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["status"] == "published"
    assert state["headline"] == "Cierre semanal"
    for column in (
        "cpi_yoy",
        "cpi_as_of",
        "unemployment_rate",
        "unrate_as_of",
        "treasury_10y",
        "dgs10_as_of",
    ):
        assert state[column] is None


def test_migrate_db_adds_macro_columns_preserving_rows(tmp_path):
    """Una base con el schema viejo gana las columnas macro sin perder datos."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO published_events (event_id, status, created_at, headline) "
            "VALUES ('weekly_close_2026-05-14', 'published', '2026-05-14', 'viejo')"
        )

    db = StateDB(db_path=str(db_path))
    state = db.get_publication_state("weekly_close_2026-05-14")

    assert state["headline"] == "viejo"
    assert state["cpi_yoy"] is None
    assert "dgs10_as_of" in state


def test_second_publish_overwrites_macro_columns(db, snapshot):
    """Semántica elegida: gana la última escritura, también para el macro.

    Test de caracterización: fija la decisión de que la fila refleje la run que
    publicó, igual que ya ocurría con `headline` e `image_url`. En la práctica
    no es alcanzable desde el orquestador —`is_published` corta la re-ejecución
    de un evento ya publicado—, pero si algún día se levanta esa guarda para
    reprocesar, esto documenta que los valores viejos no se preservan.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", macro=snapshot)
    db.mark_as_published("weekly_close_2026-08-21", macro=None)

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["cpi_yoy"] is None
    assert state["dgs10_as_of"] is None


# ── El lock y los estados terminales (ADR-009, divergencia 3) ────────────────


def test_mark_in_progress_rearms_the_lock_over_a_failed_row(db):
    """Un reintento tras un fallo tiene que volver a tomar el lock.

    `mark_in_progress` era `INSERT OR IGNORE` a secas: sobre una fila que ya
    existe no hace nada. Mientras el unico estado terminal alcanzable era
    `expired` daba igual, porque casi ninguna salida marcaba nada; en cuanto
    toda excepcion pasa a marcar `failed` (ADR-009), la fila existe siempre y
    el reintento correria **sin lock**, que es justo lo que el lock evita: dos
    runs simultaneas publicando el mismo cierre dos veces.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_failed("weekly_close_2026-08-21", reason="fmp_caido")
    assert not db.is_in_progress("weekly_close_2026-08-21")

    db.mark_in_progress("weekly_close_2026-08-21")

    assert db.is_in_progress("weekly_close_2026-08-21")


def test_mark_in_progress_rearms_the_lock_over_an_expired_row(db):
    """Lo mismo para el timeout de Telegram, que marca `expired`."""
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_expired("weekly_close_2026-08-21")

    db.mark_in_progress("weekly_close_2026-08-21")

    assert db.is_in_progress("weekly_close_2026-08-21")


def test_rearming_the_lock_preserves_the_post_ids(db):
    """Re-armar el lock no puede borrar lo que ya se publico.

    Es la mitad que hace posible la idempotencia parcial: el reintento lee
    `x_post_id` y `linkedin_post_id` inmediatamente despues de tomar el lock
    para saber que canal saltarse. Si el re-armado los pisara, republicaria en
    X un cierre que ya salio.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_x_published("weekly_close_2026-08-21", "x-123")
    db.mark_failed("weekly_close_2026-08-21", reason="linkedin_caido")

    db.mark_in_progress("weekly_close_2026-08-21")

    state = db.get_publication_state("weekly_close_2026-08-21")
    assert state["status"] == "in_progress"
    assert state["x_post_id"] == "x-123"
    assert state["linkedin_post_id"] is None


def test_mark_in_progress_never_reopens_a_published_row(db):
    """Sobre un evento ya publicado el lock no se re-arma: se queda publicado.

    El orquestador no llega aca —`is_published` corta antes— pero si alguna vez
    lo hiciera, reabrir la fila borraria la unica marca de que ese cierre ya
    salio y la run siguiente lo publicaria de nuevo.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", headline="Cierre semanal")

    db.mark_in_progress("weekly_close_2026-08-21")

    state = db.get_publication_state("weekly_close_2026-08-21")
    assert state["status"] == "published"
    assert state["headline"] == "Cierre semanal"


def test_mark_failed_does_not_undo_a_published_row(db):
    """Una excepcion despues de publicar no puede desmarcar la publicacion.

    Con "toda excepcion marca `failed`" (ADR-009) el `except` general del
    orquestador corre tambien para lo que reviente *despues* de
    `mark_as_published`. Marcar `failed` ahi seria peor que el fallo original:
    la run siguiente veria `is_published() == False` y publicaria el mismo
    cierre por segunda vez en X y LinkedIn.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", headline="Cierre semanal")

    db.mark_failed("weekly_close_2026-08-21", reason="reviento el logger")

    assert db.is_published("weekly_close_2026-08-21")


def test_mark_failed_on_an_event_that_never_started_creates_no_row(db):
    """Un abort anterior al lock no deja fila, y `mark_failed` no la inventa.

    Es lo que sostiene la primera forma de abort de ADR-009 ("aborta antes del
    lock -> ninguna fila -> la proxima run reintenta sola"): el `except`
    general llama a `mark_failed` sin saber si el lock llego a tomarse.
    """
    db.mark_failed("weekly_close_2026-08-21", reason="reviento is_published")

    assert db.get_publication_state("weekly_close_2026-08-21") == {}


# ── El hook de escritura ─────────────────────────────────────────────────────
#
# `StateDB` vive en un fichero local que no sobrevive a un entorno efimero
# (punto 11 del backlog). El hook es lo que deja que el sincronizado suba el
# fichero despues de cada escritura sin que el orquestador tenga que acordarse
# de llamarlo en seis sitios: un subconjunto elegido a mano es justo la clase
# de hueco que en este repo aparece invisible con la suite en verde.


class _Espia:
    """Cuenta las llamadas al hook. Opcionalmente revienta.

    El error se arma aparte del constructor a proposito: hay tests que
    necesitan preparar una fila con escrituras que funcionan y recien despues
    romper el hook. Armarlo de entrada haria reventar el setup y el test
    pasaria —o fallaria— por el motivo equivocado.
    """

    def __init__(self, error=None):
        self.llamadas = 0
        self._error = error

    def armar(self, error: Exception) -> None:
        self._error = error

    def __call__(self) -> None:
        self.llamadas += 1
        if self._error is not None:
            raise self._error


@pytest.fixture
def espia():
    return _Espia()


@pytest.fixture
def db_con_hook(tmp_path, espia):
    return StateDB(db_path=str(tmp_path / "state.db"), on_write=espia), espia


def test_el_hook_se_dispara_en_mark_in_progress(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    assert espia.llamadas == 1


def test_el_hook_se_dispara_en_mark_x_published(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_x_published("weekly_close_2026-08-28", "x-123")
    assert espia.llamadas == 2


def test_el_hook_se_dispara_en_mark_linkedin_published(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_linkedin_published("weekly_close_2026-08-28", "li-123")
    assert espia.llamadas == 2


def test_el_hook_se_dispara_en_mark_as_published(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_as_published("weekly_close_2026-08-28")
    assert espia.llamadas == 2


def test_el_hook_se_dispara_en_mark_failed(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_failed("weekly_close_2026-08-28", "motivo")
    assert espia.llamadas == 2


def test_el_hook_se_dispara_en_mark_expired(db_con_hook):
    db, espia = db_con_hook
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_expired("weekly_close_2026-08-28")
    assert espia.llamadas == 2


def test_el_hook_no_se_dispara_en_las_lecturas(db_con_hook):
    """Subir el fichero despues de leerlo seria gastar red por nada."""
    db, espia = db_con_hook
    db.is_published("weekly_close_2026-08-28")
    db.is_in_progress("weekly_close_2026-08-28")
    db.get_publication_state("weekly_close_2026-08-28")
    assert espia.llamadas == 0


def test_sin_hook_todo_sigue_funcionando(db):
    """`on_write` es opcional: sin R2 configurado no hay nada que sincronizar."""
    db.mark_in_progress("weekly_close_2026-08-28")
    db.mark_as_published("weekly_close_2026-08-28")
    assert db.is_published("weekly_close_2026-08-28") is True


def test_mark_failed_no_propaga_el_fallo_del_hook(tmp_path):
    """La unica escritura que se traga el error del hook, y por que.

    `mark_failed` es la unica que se llama desde dentro del `except` general
    del orquestador. Si el hook levantara ahi, la excepcion saldria del propio
    manejador de fallos y taparia la causa original — ademas de dejar la fila
    sin cerrar. Es el mismo principio documentado de `TelegramBot.send_alert()`,
    que nunca levanta porque llega cuando la publicacion ya se decidio.

    La escritura local igual se hace: lo que se pierde es la subida, no el
    registro.
    """
    espia = _Espia()
    db = StateDB(db_path=str(tmp_path / "state.db"), on_write=espia)
    db.mark_in_progress("weekly_close_2026-08-28")
    espia.armar(RuntimeError("R2 caido"))

    db.mark_failed("weekly_close_2026-08-28", "lo que sea")

    assert db.get_publication_state("weekly_close_2026-08-28")["status"] == "failed"


@pytest.mark.parametrize(
    "escritura",
    [
        lambda db: db.mark_in_progress("weekly_close_2026-08-28"),
        lambda db: db.mark_x_published("weekly_close_2026-08-28", "x-123"),
        lambda db: db.mark_linkedin_published("weekly_close_2026-08-28", "li-123"),
        lambda db: db.mark_as_published("weekly_close_2026-08-28"),
        lambda db: db.mark_expired("weekly_close_2026-08-28"),
    ],
    ids=["in_progress", "x_published", "linkedin_published", "published", "expired"],
)
def test_las_demas_escrituras_si_propagan_el_fallo_del_hook(tmp_path, escritura):
    """El resto tiene que levantar: un push perdido en silencio republica.

    `mark_expired` entra aca a proposito. Levantar deja la fila en `failed` en
    vez de `expired`, y las dos re-arman el lock igual, asi que no cuesta nada.
    """
    espia = _Espia(error=RuntimeError("R2 caido"))
    db = StateDB(db_path=str(tmp_path / "state.db"), on_write=espia)

    with pytest.raises(RuntimeError):
        escritura(db)


def test_mark_in_progress_registra_cuando_se_tomo_el_lock(db):
    """Sin `locked_at` no hay forma de distinguir un lock vivo de uno trabado.

    `created_at` no sirve para esto: no se refresca nunca (ver el test de
    abajo), así que dice cuándo nació la fila y no cuándo se tomó el lock.
    """
    db.mark_in_progress("weekly_close_2026-08-21")

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["locked_at"] is not None
    edad = datetime.now(UTC) - datetime.fromisoformat(state["locked_at"])
    assert timedelta(0) <= edad < timedelta(seconds=30)


def test_rearmar_el_lock_refresca_locked_at(db):
    """El re-arm sobre una fila `failed` es un lock nuevo, y la fecha lo dice.

    Es la mitad que hace útil al umbral. El `UPDATE` del re-arm no tocaba
    ningún timestamp, así que un reintento de hoy sobre una fila de hace tres
    semanas seguiría diciendo que el lock es de hace tres semanas — y el
    orquestador alertaría sobre la run que está corriendo en ese momento.
    """
    event = "weekly_close_2026-08-21"
    db.mark_in_progress(event)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE published_events SET status = 'failed', locked_at = ? "
            "WHERE event_id = ?",
            ("2026-08-01T00:00:00+00:00", event),
        )

    db.mark_in_progress(event)

    state = db.get_publication_state(event)
    assert state["locked_at"] != "2026-08-01T00:00:00+00:00"
    edad = datetime.now(UTC) - datetime.fromisoformat(state["locked_at"])
    assert timedelta(0) <= edad < timedelta(seconds=30)


def test_no_refresca_locked_at_si_la_fila_ya_esta_in_progress(db):
    """Una fila ya `in_progress` no es un re-arm: no hay que tocarle el lock.

    El `WHERE` de `mark_in_progress` cubre solo `failed` y `expired` a
    proposito. Si algun dia alguien lo ensancha para incluir `in_progress`
    —pensando en revivir una fila trabada— cada reintento le refrescaria el
    `locked_at` a esa misma fila, y el aviso de lock viejo no volveria a tener
    nunca una fila lo bastante antigua como para dispararse. La deteccion
    moriria en silencio, que es justo lo que existe para evitar.
    """
    event = "weekly_close_2026-08-21"
    db.mark_in_progress(event)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE published_events SET locked_at = ? WHERE event_id = ?",
            ("2026-08-01T00:00:00+00:00", event),
        )

    db.mark_in_progress(event)

    state = db.get_publication_state(event)
    assert state["status"] == "in_progress"
    assert state["locked_at"] == "2026-08-01T00:00:00+00:00"


def test_migrate_db_agrega_locked_at_en_null(tmp_path):
    """Una base vieja gana la columna, y sus filas quedan en NULL.

    Sin backfill a propósito: copiar `created_at` escribiría una aproximación
    —cuándo nació la fila, no cuándo se tomó el último lock— en la única
    columna cuyo valor entero está en ser exacta.
    """
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO published_events (event_id, status, created_at) "
            "VALUES ('weekly_close_2026-05-14', 'in_progress', '2026-05-14')"
        )

    db = StateDB(db_path=str(db_path))
    state = db.get_publication_state("weekly_close_2026-05-14")

    assert "locked_at" in state
    assert state["locked_at"] is None


def test_migrate_db_es_idempotente_con_la_columna_ya_puesta(tmp_path):
    """Abrir dos veces la misma base no revienta ni pierde el lock.

    `_migrate_db` corre en cada `StateDB(...)` y el `ALTER TABLE` de una
    columna que ya existe levanta `OperationalError`. Lo que lo hace inofensivo
    es el `except`, y esto lo fija: en un runner efímero la base baja de R2 ya
    migrada en cada corrida.
    """
    db_path = tmp_path / "state.db"
    primera = StateDB(db_path=str(db_path))
    primera.mark_in_progress("weekly_close_2026-08-21")

    segunda = StateDB(db_path=str(db_path))

    assert segunda.get_publication_state("weekly_close_2026-08-21")["locked_at"]


def test_get_locked_at_devuelve_el_momento_del_lock(db):
    """El lector devuelve un `datetime` con zona, listo para restar."""
    db.mark_in_progress("weekly_close_2026-08-21")

    locked_at = db.get_locked_at("weekly_close_2026-08-21")

    assert locked_at is not None
    assert locked_at.tzinfo is not None
    edad = datetime.now(UTC) - locked_at
    assert timedelta(0) <= edad < timedelta(seconds=30)


def test_get_locked_at_es_none_sin_fila(db):
    """Un evento que nunca se lockeó no tiene momento de lock."""
    assert db.get_locked_at("weekly_close_1999-01-01") is None


def test_get_locked_at_es_none_con_la_columna_en_null(db):
    """Una fila anterior a la migración: existe, pero no dice desde cuándo.

    El llamador no necesita separar este caso del anterior — pregunta después
    de que `is_in_progress` dijo que sí, así que en la práctica siempre es
    éste— pero los dos tienen que dar `None` y no reventar.
    """
    event = "weekly_close_2026-08-21"
    db.mark_in_progress(event)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE published_events SET locked_at = NULL WHERE event_id = ?",
            (event,),
        )

    assert db.get_locked_at(event) is None


def test_get_locked_at_devuelve_lo_guardado_y_no_la_hora_actual(db):
    """El lector tiene que leer, no inventar.

    Sin este test, un `return datetime.now(UTC)` pasa los otros tres: la
    ventana de tolerancia se cumple con una edad de cero, y los dos casos de
    `None` vuelven antes de llegar a esa línea. El aviso de lock viejo mediría
    siempre cero y no se dispararía nunca, con la suite entera en verde — que
    es exactamente el silencio que este trabajo existe para eliminar.

    La segunda fila no es decorado: con una sola, borrar el `WHERE event_id`
    devolvería igual la fila correcta. Con dos, la consulta sin filtro trae la
    otra y el test cae.
    """
    event = "weekly_close_2026-08-21"
    otro = "weekly_close_2026-08-14"
    db.mark_in_progress(otro)
    db.mark_in_progress(event)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE published_events SET locked_at = ? WHERE event_id = ?",
            ("2026-07-01T00:00:00+00:00", otro),
        )
        conn.execute(
            "UPDATE published_events SET locked_at = ? WHERE event_id = ?",
            ("2026-08-01T00:00:00+00:00", event),
        )

    assert db.get_locked_at(event) == datetime(2026, 8, 1, tzinfo=UTC)
