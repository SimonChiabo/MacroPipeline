# El estado sobrevive a un entorno efímero — plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el `state.db` sobreviva entre corridas, para que la idempotencia que promete ADR-002 se sostenga cuando el pipeline corra en un entorno efímero.

**Architecture:** el fichero SQLite entero viaja por R2. Se baja al principio de `run_weekly_close` (nunca en el constructor) y se sube después de cada escritura mutante, vía un hook en `StateDB`. La máquina de estados y el esquema no se tocan.

**Tech Stack:** Python 3.12, `boto3`, SQLite, pytest, structlog.

**Spec:** `docs/superpowers/specs/2026-08-31-state-survives-ephemeral-runs-design.md`

---

## Estructura de ficheros

| Fichero | Responsabilidad | Nuevo |
|---|---|---|
| `src/macro_pipeline/storage/r2_client.py` | Get/put genérico de objetos, además del `upload_image` que ya tiene. | no |
| `src/macro_pipeline/storage/state_sync.py` | `pull()` / `push()` del fichero de estado. Traduce los errores de R2 a la tabla de decisión. | sí |
| `src/macro_pipeline/storage/state.py` | Hook `on_write` tras cada escritura mutante. Sin cambios de esquema ni de semántica. | no |
| `src/macro_pipeline/orchestration/main.py` | Pull al arrancar, rama nueva en el punto de decisión, cableado del hook. | no |
| `tests/unit/test_r2_client.py` | El get/put genérico y la distinción key-ausente / transporte. **Hoy `r2_client.py` no tiene ningún test unitario.** | sí |
| `tests/unit/test_state_sync.py` | La tabla de decisión: transporte, `NoSuchKey`, descarga rota. | sí |
| `tests/unit/test_state.py` | Que el hook se dispara en las seis escrituras y en ninguna lectura. | no |
| `tests/integration/test_orchestrator_state_sync.py` | Orden del pull, el guard apagado, la no-recursión en el manejador. | sí |
| `docs/adr/009-degradation-policy.md` | La fila de R2 se parte: imagen (opcional) y estado (necesario). | no |
| `docs/adr/002-claude-routines.md`, `docs/adr/007-*.md` | Anotar de qué depende ahora la idempotencia. | no |
| `.env.example`, `README.md` | Que R2 pasa a sostener el estado. | no |

**Por qué un hook en `StateDB` y no seis llamadas en `main.py`:** el push tiene que acompañar a la escritura, y un subconjunto elegido a mano es justo la clase de hueco que en este repo aparece invisible con la suite en verde. Con el hook, "¿se sube después de escribir?" se testea una vez por método en el sitio donde está la escritura.

**Por qué el pull no va en `StateDB.__init__`:** la causa raíz del punto 13 fue que el orden de construcción hacía de lógica. Además `__init__` corre antes de que exista el canal de alerta. Detalle que lo hace posible: cada método de `StateDB` abre su propio `sqlite3.connect` y no sostiene conexión, así que pisar el fichero al arrancar la corrida funciona aunque `__init__` ya haya pasado.

---

## La tabla de decisión, que gobierna todo el plan

| Caso | Qué hace | Dónde vive |
|---|---|---|
| Pull falla por transporte | Anota el motivo; el guard de duplicados **no corre**; el punto de decisión alerta y sale `1` **antes del lock** | `state_sync.py` + `_startup_exit_code` |
| Pull devuelve `NoSuchKey` | **Deja el fichero local como está**, sigue, y alerta "estado remoto ausente: primera corrida o pérdida" | idem |
| Push falla | Levanta `StateSyncError` → manejador general → `mark_failed` → alerta → `exit 1` | `state_sync.py` |
| Push falla **dentro de `mark_failed`** | Best-effort: se loguea y **no levanta** | `state.py` |

**`NoSuchKey` no borra el fichero local a propósito.** En un runner el local está vacío igual; en la máquina de Simon, el estado que ya existe siembra el remoto en el primer push. Es el camino de migración gratis.

**La única escritura que no levanta es `mark_failed`**, porque es la única que se llama desde dentro del `except` general. Es el mismo principio documentado de `TelegramBot.send_alert()`, que nunca levanta porque llega cuando la publicación ya se decidió. `mark_expired` sí puede levantar: la fila queda `failed` en vez de `expired` y las dos re-arman el lock igual.

---

## Task 1: get/put genérico en `R2Client`

**Files:**
- Modify: `src/macro_pipeline/storage/r2_client.py`
- Create: `tests/unit/test_r2_client.py`

**Verificado el 2026-08-31: `r2_client.py` no tiene ningún test unitario.** Lo único que lo toca es `tests/integration/test_orchestrator_persistence.py`, de refilón. Estamos por convertirlo en la pieza de la que depende la deduplicación, así que el fichero de tests nace acá.

- [ ] **Step 1: Tests que fallan**

Cubrir: `put_object` con bytes arbitrarios y content-type; `get_object` devolviendo bytes; y **la distinción que sostiene la tabla entera** — una key ausente contra un fallo de transporte.

- [ ] **Step 2: Implementar**

Añadir `download_object(key) -> bytes | None` (None si no existe) y `upload_object(key, body, content_type)`. `upload_image` pasa a delegar en `upload_object` sin cambiar su firma ni su retorno.

- [ ] **Step 3: Verificar la forma real del error**

**No confiar en la memoria acá.** Comprobar contra botocore/R2 si una key ausente llega como `NoSuchKey` o como `404` en `e.response["Error"]["Code"]`, y que un corte de red sale como `EndpointConnectionError` de botocore y no como `ClientError` — es la misma razón por la que el `except` de `upload_image` es ancho a propósito (ADR-009, divergencia b). Distinguir esos dos casos **es** la tabla de decisión.

- [ ] **Step 4:** `./.venv/Scripts/python.exe -m pytest tests/unit/test_r2_client.py -q`

---

## Task 2: `StateSync`

**Files:**
- Create: `src/macro_pipeline/storage/state_sync.py`
- Test: `tests/unit/test_state_sync.py`

- [ ] **Step 1: Tests que fallan**

Uno por fila de la tabla de decisión, más: que `pull()` con `NoSuchKey` **deja intacto** un fichero local existente, y que una descarga rota no corrompe el local.

- [ ] **Step 2: Implementar**

`StateSync(r2_client, db_path, key="state/state.db")` con:
- `pull() -> str | None` — devuelve el motivo del fallo de transporte, o `None` si fue bien o si la key no existía. **Nunca levanta**: anota, no reporta. Quien decide es el punto de decisión.
- `push()` — levanta `StateSyncError` si falla.
- `remote_absent: bool` — para que el punto de decisión distinga "primera corrida o pérdida" de una corrida normal.

**`pull()` baja a un fichero temporal y renombra sobre el local.** Una descarga cortada a la mitad no puede dejar corrupto el estado de la máquina de Simon.

- [ ] **Step 3:** Tras el rename, volver a correr `_init_db()` y `_migrate_db()` sobre el fichero bajado — corrieron sobre el vacío, no sobre este. Son idempotentes a propósito.

- [ ] **Step 4:** `./.venv/Scripts/python.exe -m pytest tests/unit/test_state_sync.py -q`

---

## Task 3: el hook de escritura en `StateDB`

**Files:**
- Modify: `src/macro_pipeline/storage/state.py`
- Test: `tests/unit/test_state.py`

- [ ] **Step 1: Tests que fallan**

Que el hook se dispara en las **seis** escrituras mutantes (`mark_in_progress`, `mark_x_published`, `mark_linkedin_published`, `mark_as_published`, `mark_failed`, `mark_expired`) y en **ninguna** lectura (`is_published`, `is_in_progress`, `get_publication_state`). Un test por método, no uno agregado: un test agregado no distingue cuál falta.

Y el caso que evita la recursión: **con el hook levantando, `mark_failed` no propaga** y deja el warning en el log.

- [ ] **Step 2: Implementar**

`__init__(self, db_path=None, on_write=None)`. Un `_notify_write()` privado al final de cada escritura mutante. En `mark_failed`, envuelto en `try/except` con log — con un comentario que diga **por qué** es la única excepción, porque sin eso el próximo lector lo lee como una inconsistencia.

- [ ] **Step 3: Verificación por mutación**

Quitar la llamada a `_notify_write()` de **cada** método, de a uno: tiene que caer exactamente un test cada vez. Si sacarla de alguno deja la suite verde, ese método no está cubierto.

- [ ] **Step 4:** `./.venv/Scripts/python.exe -m pytest tests/unit/test_state.py -q`

---

## Task 4: cableado en el orquestador

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/integration/test_orchestrator_state_sync.py`

- [ ] **Step 1: Tests que fallan**

1. Pull con fallo de transporte → **no** se consulta `is_published`, sale `1`, **no hay fila** y la alerta nombra el estado.
2. `NoSuchKey` → la corrida sigue y alerta "primera corrida o pérdida".
3. Push fallando en `mark_x_published` → la fila queda `failed` y la alerta nombra el riesgo de duplicado.
4. **El orden de las ramas** de `_startup_exit_code`: sin Telegram y con el pull roto a la vez, gana la rama de Telegram. Docstring explicando que el orden es obligatorio, espejo del test que dejó el punto 13.
5. Sin R2 configurado → no sincroniza, y todo lo de hoy sigue igual.

- [ ] **Step 2: Implementar**

En `__init__`: construir `StateSync` si `self.r2 is not None`, y pasarle el hook a `StateDB`. **Anotar motivos, no reportar** — igual que `switch_errors` y `component_errors`.

Al principio de `run_weekly_close`, antes del guard de duplicados:

```python
# El pull va acá y no en el constructor: el punto 13 dejó dicho que el orden
# de construcción no debe hacer de lógica, y acá todavía no existe el canal.
# No reporta: anota. Quien decide es `_startup_exit_code`.
self.state_sync_error = self._pull_remote_state()

# El guard solo corre si el estado es confiable. Con el pull roto, consultar
# `is_published` sobre una base vacía respondería que no se publicó nunca.
if self.state_sync_error is None and self.state.is_published(event_id):
    logger.info("event_already_published_skipping", event_id=event_id)
    return 0
```

En `_startup_exit_code`, la rama nueva va **después** de la de Telegram: no se puede avisar de un pull roto sin canal.

- [ ] **Step 3:** `./.venv/Scripts/python.exe -m pytest tests/ -q` y `./.venv/Scripts/python.exe -m mypy src`

---

## Task 5: los ADR

**Files:**
- Modify: `docs/adr/009-degradation-policy.md`, `docs/adr/002-claude-routines.md`, `docs/adr/007-*.md`

- [ ] **Step 1: Partir la fila de R2 en la tabla de ADR-009**

Hoy hay una fila para R2. Pasan a ser dos, porque **el estatus cambia para el estado, no para la imagen**:
- *R2 / snapshot de imagen* — sigue opcional, degrada, no alerta. Sin cambios.
- *R2 / estado* — **necesario**: caído aborta antes del lock y alerta. El tercer eje no le aplica.

Anotar en §Consecuencias el fail-silent que queda abierto: un workflow programado sin los secrets de R2 corre local-only y en silencio. **Cuando se escriba ese workflow, los secrets de R2 van en su paso de pre-chequeo.**

- [ ] **Step 2:** ADR-002: su implicación de resiliencia ahora depende de que el estado se sincronice. ADR-007: la reproducibilidad también.

- [ ] **Step 3: Recorrer la tabla de ADR-009 fila por fila contra el código.**

No es opcional. Ese ejercicio destapó tres divergencias la primera vez, una fila mentirosa de R2 la segunda y dos más la tercera. Es el control que más ha rendido en este repo, y este trabajo toca justo la política.

---

## Task 6: documentación y cierre

- [ ] **Step 1:** `.env.example` y `README.md`: R2 pasa a sostener el estado, no solo los snapshots.
- [ ] **Step 2:** CI verde sobre el HEAD exacto — el run, no "CI configurado". Anotar número de run, conteo de tests y cobertura.
- [ ] **Step 3:** Actualizar `macropipeline-pending-work.md`: cerrar el punto 11, y anotar aparte las dos cosas que quedan abiertas a propósito — la alerta sobre `in_progress` viejo y los secrets de R2 en el futuro workflow programado.

---

## Preguntas abiertas

**Si Routines persiste el workspace.** No bloquea nada de este plan: fija la urgencia, no el arreglo. En las dos ramas —y en el Plan B de GitHub Actions, efímero por construcción— el estado tiene que salir del disco local.
