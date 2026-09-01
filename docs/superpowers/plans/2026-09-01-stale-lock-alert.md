# Alertar sobre un lock `in_progress` viejo — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que una fila trabada en `in_progress` deje de saltarse el cierre semanal en silencio, avisando por Telegram cuando el lock es demasiado viejo para pertenecer a una run viva.

**Architecture:** una columna nueva `locked_at` registra cuándo se tomó el lock —en las dos ramas de `mark_in_progress`, incluido el re-arm, que hoy no toca ningún timestamp—; un lector en `StateDB` la devuelve cruda; y el orquestador aplica la política en el guard de lock: si el lock lleva más de dos horas, o si no se sabe desde cuándo, alerta antes de devolver `0`. No toma el lock, no lo expira y no cambia el `return 0`.

**Tech Stack:** Python 3.12, SQLite (`sqlite3` de stdlib), pytest, `ruff` (line-length 88), `mypy --strict`.

**Diseño:** `docs/superpowers/specs/2026-09-01-stale-lock-alert-design.md`

**Intérprete:** siempre `./.venv/Scripts/python.exe -m ...`. Nunca `python` a secas.

---

## Estructura de ficheros

| Fichero | Responsabilidad | Tarea |
|---|---|---|
| `src/macro_pipeline/storage/state.py` | Registrar cuándo se tomó el lock y devolverlo sin interpretarlo | 1, 2 |
| `src/macro_pipeline/orchestration/main.py` | La política: qué antigüedad cuenta como vieja, y el aviso | 3 |
| `docs/adr/009-degradation-policy.md` | La cuarta forma de abortar deja de ser silenciosa | 4 |

**Nada que tocar en `state_sync.py`:** el sincronizado sube y baja el fichero
`.db` entero, así que una columna nueva viaja sola. Verificado: no hay ninguna
consulta con columnas enumeradas fuera de `state.py`.

**Nada que tocar en `is_in_progress`:** es el guard, responde la única pregunta
que le corresponde y tiene tests. La antigüedad la sirve un lector aparte.

---

## Task 1: `locked_at` — la columna, la migración y las dos ramas del lock

**Files:**
- Modify: `src/macro_pipeline/storage/state.py:1-10` (imports), `:62-88` (schema), `:93-110` (migración), `:131-158` (`mark_in_progress`)
- Test: `tests/unit/test_state.py`

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/unit/test_state.py`, ampliar el import de `datetime` que ya está en la
cabecera del fichero:

```python
from datetime import date, datetime, timedelta, timezone
```

Y agregar al final del fichero:

```python
def test_mark_in_progress_registra_cuando_se_tomo_el_lock(db):
    """Sin `locked_at` no hay forma de distinguir un lock vivo de uno trabado.

    `created_at` no sirve para esto: no se refresca nunca (ver el test de
    abajo), así que dice cuándo nació la fila y no cuándo se tomó el lock.
    """
    db.mark_in_progress("weekly_close_2026-08-21")

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["locked_at"] is not None
    edad = datetime.now(timezone.utc) - datetime.fromisoformat(state["locked_at"])
    assert edad < timedelta(seconds=30)


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
    edad = datetime.now(timezone.utc) - datetime.fromisoformat(state["locked_at"])
    assert edad < timedelta(seconds=30)


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
```

`sqlite3`, `pytest`, `StateDB` y `_OLD_SCHEMA` ya están en el fichero; sólo se
amplía el import de `datetime`.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest -v tests/unit/test_state.py::test_mark_in_progress_registra_cuando_se_tomo_el_lock tests/unit/test_state.py::test_rearmar_el_lock_refresca_locked_at tests/unit/test_state.py::test_migrate_db_agrega_locked_at_en_null tests/unit/test_state.py::test_migrate_db_es_idempotente_con_la_columna_ya_puesta`

**Por qué los cuatro node id enteros y no un `-k`:** `-k "lock"` barre tres
tests que ya existen y ya pasan —`test_mark_in_progress_rearms_the_lock_over_a_failed_row`
y los otros dos de `test_state.py:130-159`—, y una fase roja mezclada con verdes
ajenos no se puede leer.

Expected: FAIL, los cuatro, **cada uno por su motivo**. Vale la pena mirar cuál
da cuál, porque no son el mismo error:

- `test_mark_in_progress_registra_cuando_se_tomo_el_lock` → `KeyError: 'locked_at'`.
  `get_publication_state` hace `SELECT *` y hoy esa columna no existe, así que
  la clave no está en el dict.
- `test_rearmar_el_lock_refresca_locked_at` → `sqlite3.OperationalError: no such
  column: locked_at`, **en el setup**: el `UPDATE` a mano que ensucia la fila
  nombra la columna, así que el test muere antes de llegar a ninguna aserción.
- `test_migrate_db_agrega_locked_at_en_null` → `AssertionError` en
  `assert "locked_at" in state`.
- `test_migrate_db_es_idempotente_con_la_columna_ya_puesta` → `KeyError: 'locked_at'`.

- [ ] **Step 3: Agregar la columna al schema y a la migración**

En `src/macro_pipeline/storage/state.py`, ampliar el import de `datetime`:

```python
from datetime import datetime, timezone
```

En `_init_db`, agregar la columna justo después de `created_at`:

```python
                    created_at          TIMESTAMP NOT NULL,
                    locked_at           TEXT,
                    published_at        TIMESTAMP,
```

En `_migrate_db`, agregar una entrada más a la lista `new_cols`, al final:

```python
            ("x_post_id", "TEXT"),
            ("linkedin_post_id", "TEXT"),
            ("locked_at", "TEXT"),
        ]
```

**Por qué `TEXT` y no `TIMESTAMP`:** un `TIMESTAMP` con un objeto `datetime`
pasa por el adaptador por defecto de `sqlite3`, deprecado desde Python 3.12 y
que ya emite `DeprecationWarning` en esta suite (`state.py:234`). Código nuevo no
se suma a un camino deprecado. El resto de la tabla ya guarda fechas como texto
(`cpi_as_of`, `unrate_as_of`, `dgs10_as_of`).

- [ ] **Step 4: Escribir `locked_at` en las dos ramas de `mark_in_progress`**

Reemplazar el cuerpo de `mark_in_progress` (`state.py:131-158`) entero,
docstring incluido:

```python
    def mark_in_progress(self, event_id: str) -> None:
        """Toma el lock del evento, tambien si una run anterior lo dejo cerrado.

        El `INSERT OR IGNORE` solo cubre la primera run: sobre una fila que ya
        existe no hace nada. Desde que toda excepcion marca `failed` (ADR-009)
        la fila existe en cada reintento, asi que hace falta el `UPDATE` o el
        reintento correria sin lock.

        El `WHERE` es deliberadamente estrecho: solo `failed` y `expired`. Una
        fila `published` no se reabre —seria borrar la unica marca de que ese
        cierre ya salio— y una `in_progress` no se toca, que es lo que hace que
        `is_in_progress` siga sirviendo de guarda contra runs simultaneas.
        Las columnas de `post_id` quedan como estan a proposito: son lo que lee
        la reconciliacion para saber que canal saltarse.

        `locked_at` se escribe en las **dos** ramas. Refrescarlo en el re-arm es
        lo que hace util al umbral del orquestador: sin eso, un reintento de hoy
        sobre una fila de hace tres semanas parece un lock de hace tres semanas
        y la run viva se alerta a si misma. `created_at` no se toca — sigue
        diciendo cuando nacio la fila, que es lo que su nombre promete.
        """
        ahora = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO published_events "
                "(event_id, status, created_at, locked_at) "
                "VALUES (?, 'in_progress', ?, ?)",
                (event_id, datetime.utcnow(), ahora),
            )
            conn.execute(
                "UPDATE published_events SET status = 'in_progress', locked_at = ? "
                "WHERE event_id = ? AND status IN ('failed', 'expired')",
                (ahora, event_id),
            )
        logger.info("event_marked_in_progress", event_id=event_id)
        self._notify_write()
```

**Nota sobre `datetime.utcnow()` en el `INSERT`:** se deja como está. Cambiarlo
tocaría `created_at`, que no es este trabajo, y mezclaría dos cosas en un mismo
diff. La deprecación de esa llamada es un asunto aparte.

- [ ] **Step 5: Correr los tests para verificar que pasan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_state.py -v`

Expected: PASS, todos. Los tests viejos del fichero no cambian: ninguno lee
`created_at` ni enumera columnas.

- [ ] **Step 6: Correr la suite entera — esto toca el schema compartido**

Run: `./.venv/Scripts/python.exe -m pytest -q`

Expected: PASS. Cualquier test que enumere columnas a mano saldría acá.

- [ ] **Step 7: Los tres gates**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: los tres limpios.

- [ ] **Step 8: Commit**

```bash
git add src/macro_pipeline/storage/state.py tests/unit/test_state.py
git commit -m "feat(state): locked_at registra cuando se tomo el lock"
```

---

## Task 2: el lector que devuelve el dato sin interpretarlo

**Files:**
- Modify: `src/macro_pipeline/storage/state.py` (método nuevo, después de `is_in_progress`)
- Test: `tests/unit/test_state.py`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `tests/unit/test_state.py`:

```python
def test_get_locked_at_devuelve_el_momento_del_lock(db):
    """El lector devuelve un `datetime` con zona, listo para restar."""
    db.mark_in_progress("weekly_close_2026-08-21")

    locked_at = db.get_locked_at("weekly_close_2026-08-21")

    assert locked_at is not None
    assert locked_at.tzinfo is not None
    assert datetime.now(timezone.utc) - locked_at < timedelta(seconds=30)


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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest -v tests/unit/test_state.py::test_get_locked_at_devuelve_el_momento_del_lock tests/unit/test_state.py::test_get_locked_at_es_none_sin_fila tests/unit/test_state.py::test_get_locked_at_es_none_con_la_columna_en_null`

Expected: FAIL, los tres, con `AttributeError: 'StateDB' object has no attribute 'get_locked_at'`.

(Node id enteros por el mismo motivo que en la Task 1: `-k` con un substring
corto barre tests ajenos que ya pasan.)

- [ ] **Step 3: Implementar el lector**

En `src/macro_pipeline/storage/state.py`, justo después de `mark_in_progress` y
antes del separador `# ── Publicado ──`:

```python
    def get_locked_at(self, event_id: str) -> datetime | None:
        """Cuando se tomo el lock de este evento, o `None` si no se sabe.

        Devuelve el dato crudo y no lo interpreta a proposito: que antiguedad
        cuenta como «vieja» sale del timeout de aprobacion humana, que es una
        decision del pipeline. Poner el umbral aca obligaria a esta capa a
        saber cuanto tarda una run sana.

        `None` cubre dos casos que el llamador no necesita separar: la fila no
        existe, o es anterior a la columna `locked_at`. El unico llamador
        pregunta despues de que `is_in_progress` dijo que si, asi que en la
        practica es siempre el segundo — y ese caso alerta, porque una fila
        trabada con antiguedad desconocida no va a recibir un `locked_at`
        nunca: `mark_in_progress` no toca las filas `in_progress`.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT locked_at FROM published_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_state.py -v`

Expected: PASS, todos.

- [ ] **Step 5: Los tres gates**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: los tres limpios. El `from __future__ import annotations` de la
cabecera hace válida la anotación `datetime | None` sin más.

- [ ] **Step 6: Commit**

```bash
git add src/macro_pipeline/storage/state.py tests/unit/test_state.py
git commit -m "feat(state): get_locked_at sirve la antiguedad sin interpretarla"
```

---

## Task 3: el umbral y la alerta

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:1-5` (import), `:60-75` (constante nueva), `:589` (método nuevo), `:656-661` (el guard)
- Test: `tests/integration/test_orchestrator_exit_states.py`

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/integration/test_orchestrator_exit_states.py`, ampliar los imports de
la cabecera:

```python
import sqlite3
from datetime import date, datetime, timedelta, timezone
```

Agregar el helper justo después de `_build_orchestrator`:

```python
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
```

Y los tres tests al final del fichero:

```python
def test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre(data, state):
    """La cuarta forma de abortar de ADR-009 deja de ser silenciosa.

    Sin esto, la fila trabada se salta el cierre semana tras semana y la única
    señal es una línea de log en un runner efímero que nadie mira.
    """
    orch = _build_orchestrator(data, state)
    viejo = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
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
    reciente = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    _trabar_el_lock(state, reciente)

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest -v tests/integration/test_orchestrator_exit_states.py::test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre tests/integration/test_orchestrator_exit_states.py::test_un_lock_sin_fecha_alerta tests/integration/test_orchestrator_exit_states.py::test_un_lock_reciente_no_alerta`

**No usar `-k "lock"` acá:** además de los tres de arriba matchea
`test_a_broken_macro_block_still_publishes_and_warns` y
`test_a_run_without_the_llm_layer_publishes_the_generic_block` —«b-**lock**»—,
que pasan hoy y ensucian la lectura de la fase roja.

Expected: **dos de los tres fallan, y el tercero pasa desde el principio.** Es
importante mirar cuál es cuál y no leer «2 failed» como si fuera lo esperado
para los tres:

- `test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre` y
  `test_un_lock_sin_fecha_alerta` fallan con
  `AssertionError: Expected 'send_alert' to have been called once. Called 0 times.`
  — hoy el guard devuelve `0` sin avisar a nadie.
- `test_un_lock_reciente_no_alerta` **PASA ya**, porque hoy no se alerta nunca.
  Es un test de guardia contra el over-alerting, no de la funcionalidad nueva:
  su trabajo empieza en el Step 4, donde tiene que seguir pasando. Si después de
  implementar se pone rojo, el umbral quedó mal.

Para confirmar que ese tercero está anclando algo de verdad y no es decorativo,
al terminar el Step 4 hay una comprobación explícita en el Step 5.

- [ ] **Step 3: Agregar la constante del umbral**

En `src/macro_pipeline/orchestration/main.py`, ampliar el import de `datetime`
de la cabecera:

```python
from datetime import date, datetime, timezone
```

Y agregar la constante justo después del bloque de `_CONSECUENCIA`
(`main.py:64-75`), antes de `def _generic_headline`:

```python
# Cuanto puede durar un lock sano. No es un numero elegido a ojo: la fase de
# aprobacion espera al humano con `wait_for_approval(..., timeout_seconds=3600)`
# (mas abajo en este mismo fichero), asi que una hora entera de `in_progress` es
# un estado perfectamente normal. Dos horas son ese timeout mas el resto del
# pipeline con margen. Si algun dia se toca ese 3600, este numero es lo otro que
# hay que mirar: lo que importa es la relacion, no el valor.
_LOCK_VIEJO_SEGUNDOS = 7200
```

- [ ] **Step 4: Implementar el aviso y engancharlo al guard**

En `src/macro_pipeline/orchestration/main.py`, agregar el método justo antes de
`def run_weekly_close` (que hoy empieza en `main.py:590`):

```python
    def _avisar_lock_trabado(self, event_id: str, telegram: TelegramBot) -> None:
        """Avisa si el lock no puede pertenecer a una run viva.

        ADR-009 clasifica la fila trabada como la forma de abortar que no se
        acepta, y no se puede eliminar: una muerte no atrapable —SIGKILL, el
        runner que se apaga— la deja asi por definicion y ningun `except` la
        cubre. Lo que si se puede es que deje de saltarse el cierre en
        silencio, semana tras semana.

        No toma el lock ni lo expira, a proposito: el umbral dice que una run
        viva es *improbable*, no *imposible*, y auto-expirar un lock ajeno es
        el camino a publicar el mismo cierre dos veces — el peor resultado
        posible de este sistema.
        """
        locked_at = self.state.get_locked_at(event_id)
        if locked_at is None:
            desde = "y no se sabe desde cuándo: la fila es anterior a la columna"
        else:
            segundos = (datetime.now(timezone.utc) - locked_at).total_seconds()
            if segundos <= _LOCK_VIEJO_SEGUNDOS:
                return
            desde = f"desde hace {segundos / 3600:.1f} horas"

        logger.warning("stale_lock_detected", event_id=event_id)
        telegram.send_alert(
            f"⚠️ El cierre `{event_id}` se está salteando: la fila quedó "
            f"trabada en `in_progress` {desde}.\n\n"
            "Ninguna corrida futura lo va a publicar mientras siga así: el "
            "guard de lock corta antes de tocar nada.\n\n"
            "No se repara solo a propósito. Expirar un lock que podría "
            "pertenecer a una run viva es el camino a publicar el mismo cierre "
            "dos veces, así que hace falta revisar el estado a mano."
        )
```

Y cambiar el guard (`main.py:656-661`) para que lo llame antes del `return`:

```python
                # ── Locking ligero: evitar runs simultáneas ────────────────────
                # El aviso va antes del `return`: la fila trabada es la forma de
                # abortar que ADR-009 no acepta, y hasta acá se saltaba el cierre
                # sin decirselo a nadie.
                if self.state.is_in_progress(event_id):
                    self._avisar_lock_trabado(event_id, telegram)
                    logger.warning(
                        "pipeline_already_running_skipping", event_id=event_id
                    )
                    return 0
```

- [ ] **Step 5: Correr los tests, y comprobar que el tercero anclaba algo**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v`

Expected: PASS, todos — los tres nuevos y los viejos del fichero.

Después, la comprobación del test que nació verde. Cambiar **a mano y
temporalmente** el `<=` de `_avisar_lock_trabado` por `>=`:

```python
            if segundos >= _LOCK_VIEJO_SEGUNDOS:
                return
```

Run: `./.venv/Scripts/python.exe -m pytest -v tests/integration/test_orchestrator_exit_states.py::test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre tests/integration/test_orchestrator_exit_states.py::test_un_lock_sin_fecha_alerta tests/integration/test_orchestrator_exit_states.py::test_un_lock_reciente_no_alerta`

Expected: **dos rojos y un verde**, y los tres son la comprobación:

- `test_un_lock_reciente_no_alerta` FALLA con `AssertionError: Expected
  'send_alert' to not have been called. Called 1 times.` — es el que interesa:
  prueba que ese test guarda el umbral y no es decorativo.
- `test_un_lock_viejo_alerta_antes_de_saltarse_el_cierre` **también falla**, y
  es correcto: con `>=` un lock de 5 h se va por el `return` temprano y deja de
  alertar. No es que se haya roto otra cosa.
- `test_un_lock_sin_fecha_alerta` sigue en verde: la rama del NULL no consulta
  el umbral.

**Revertir el `>=` a `<=` y volver a correr para confirmar que los tres vuelven
a PASS.**

- [ ] **Step 6: Correr la suite entera**

Run: `./.venv/Scripts/python.exe -m pytest -q`

Expected: PASS. Interesa especialmente `tests/unit/test_orchestrator_startup.py`,
que ejercita el mismo camino de arranque.

- [ ] **Step 7: Los tres gates**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: los tres limpios.

- [ ] **Step 8: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(orchestration): un lock trabado deja de saltarse el cierre en silencio"
```

---

## Task 4: ADR-009 deja de decir «en silencio»

**Files:**
- Modify: `docs/adr/009-degradation-policy.md:64-80`

Sin tests: es documentación. Lo que la hace obligatoria es que la frase
«se salta en silencio» pasa a ser **falsa** en cuanto la Task 3 está en `main`,
y una política que describe mal el código es peor que no tenerla.

- [ ] **Step 1: Corregir la fila de la tabla del segundo eje**

En `docs/adr/009-degradation-policy.md`, la fila que hoy dice:

```markdown
| Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo `event_id` se salta en silencio |
```

pasa a decir:

```markdown
| Aborta trabado | `in_progress` | **No se acepta**: el reintento del mismo `event_id` se salta el cierre, y alerta si el lock lleva más de dos horas |
```

- [ ] **Step 2: Ampliar el párrafo que sigue a la tabla**

Después del párrafo que empieza «Un abort **nunca** debe dejar la fila en
`in_progress`…», agregar:

```markdown
Esa cuarta forma no se puede eliminar: una muerte no atrapable —SIGKILL, el
runner efímero que se apaga— deja la fila trabada por definición, y ningún
`except` la cubre. Lo que sí se eliminó es el silencio. Desde el 2026-09-01 el
guard de lock avisa por Telegram cuando el lock lleva más de dos horas, o
cuando la fila es anterior a la columna `locked_at` y no se sabe desde cuándo.

El umbral sale del timeout de aprobación humana (`wait_for_approval`, 3600 s):
una hora de `in_progress` es un estado sano mientras el operador decide, así
que sólo se alerta bastante por encima de eso.

**Alertar no vuelve aceptable a esa forma de abortar: la vuelve visible.** El
lock no se expira solo, y es deliberado — el umbral dice que una run viva es
improbable, no imposible, y auto-expirar un lock ajeno es el camino a publicar
el mismo cierre dos veces.
```

- [ ] **Step 3: Verificar que no queda ninguna otra copia de la frase vieja**

Run: `grep -rn "en silencio" docs/adr/009-degradation-policy.md`

Expected: ninguna línea que describa la fila `in_progress` del segundo eje como
silenciosa. Otras apariciones de «en silencio» en el documento —la segunda forma
de abortar, que es el apagado por switch— son correctas y se quedan.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/009-degradation-policy.md
git commit -m "docs(adr): la fila trabada se salta el cierre, pero ya no en silencio"
```

---

## Cierre

- [ ] **Step 1: Marcar el plan como completo y empujar**

Run: `./.venv/Scripts/python.exe -m pytest -q && git push`

Expected: PASS y push limpio. Después, confirmar el run de CI **sobre el HEAD
exacto** con `gh run list -L 3` antes de dar el trabajo por cerrado: verde en
local no es verde en Actions.

- [ ] **Step 2: Actualizar la memoria del backlog**

`macropipeline-pending-work.md` lista esta mejora como pendiente («alertar sobre
una fila `in_progress` vieja»). Al cerrarla hay que anotar el commit y el run de
CI, y **corregir la línea que dice que ADR-009:70 dice "se salta en silencio"**,
porque deja de ser cierta.

Los otros tres residuos siguen abiertos y no los toca este trabajo: los dos del
sincronizado y la divergencia 7. El primero del sincronizado queda **más
visible** —termina cayendo en este mismo guard— pero no cerrado: el cierre se
sigue salteando.
