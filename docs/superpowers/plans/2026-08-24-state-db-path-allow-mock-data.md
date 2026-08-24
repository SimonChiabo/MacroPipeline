# Decidir STATE_DB_PATH y ALLOW_MOCK_DATA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dejar la seccion `.env vs .env.example` de `python scripts/check_publishers.py` sin un solo `[AVISO]`, decidiendo las dos variables que hoy reporta y borrando la base de datos huerfana de la raiz.

**Architecture:** no se toca codigo de produccion. `STATE_DB_PATH` pasa a declaracion comentada en `.env.example`, que la mete en `documentadas` via `_commented_names()` sin meterla en `ejemplo` — el mismo mecanismo que ya cubre `LINKEDIN_TOKEN_ISSUED`. `ALLOW_MOCK_DATA` se escribe explicita en `.env`. Un test fija las dos decisiones para que no se deshagan solas.

**Tech Stack:** Python 3.12, pytest, ruff, mypy. Entorno local: `.venv/Scripts/python.exe` (Windows).

**Spec:** `docs/superpowers/specs/2026-08-24-state-db-path-allow-mock-data-design.md`

---

## Estructura de ficheros

| Fichero | Que cambia | Se commitea |
|---|---|---|
| `tests/unit/test_check_publishers.py` | reescribir el docstring de `test_runs_against_the_real_repo_files` (lineas 145-164) y agregar dos asserts | si |
| `.env.example` | comentar la declaracion de `STATE_DB_PATH` y reescribir su comentario | si |
| `.env` | agregar `ALLOW_MOCK_DATA=false` | **no** — esta en `.gitignore:2` |
| `macropipeline.db` | borrar | **no** — untracked y en `.gitignore` (`*.db`) |
| memoria del proyecto | cerrar el punto 5, corregir un hecho falso, abrir un punto nuevo | no (vive fuera del repo) |

`scripts/check_publishers.py` **no se toca**. Esa es la comprobacion de que el
diseno es correcto: si hiciera falta cambiar el script, la decision estaria mal.

---

### Task 1: fijar las dos decisiones en el test y hacerlas ciertas

**Files:**
- Modify: `tests/unit/test_check_publishers.py:145-164`
- Modify: `.env.example:44-47`
- Modify: `.env` (no se commitea)

El test va primero y falla por las dos variables a la vez. Cada edicion de
fichero apaga exactamente uno de los dos fallos, y se corre el test en medio
para verlo.

- [ ] **Step 1: Reemplazar el test entero**

Reemplazar las lineas 145-164 de `tests/unit/test_check_publishers.py`
(la funcion `test_runs_against_the_real_repo_files` completa, docstring
incluido) por:

```python
def test_runs_against_the_real_repo_files(check_publishers):
    """Humo sobre los ficheros de verdad: se parsean y devuelven hallazgos.

    A proposito NO se exige cero deriva. Convertir esto en un gate de deriva
    cero es la misma trampa que ya se evito en el codigo de salida del
    script: la primera variable que se agregue al `.env` antes de
    documentarla rompe los tests locales, y asi es como alguien termina
    desactivando el chequeo.

    Lo que si se fija son las decisiones ya tomadas, una por variable.
    `STATE_DB_PATH` y `ALLOW_MOCK_DATA` se decidieron el 2026-08-24
    (ver `docs/superpowers/specs/`): la primera queda declarada comentada en
    el ejemplo, porque su default —`~/.macropipeline/state.db`— ya es
    absoluto y ajeno al CWD (ADR-007 pide que la variable sea configurable,
    que es cosa distinta: para eso alcanza con que exista); la
    segunda va explicita en el `.env`, porque decide si se puede publicar con
    datos sinteticos y eso no deberia depender de un default del codigo.
    """
    real = ROOT / ".env"
    if not real.exists():
        pytest.skip("sin .env local: nada que comparar")

    hallazgos = check_publishers.compare_env_files(ROOT / ".env.example", real)

    assert isinstance(hallazgos, list)
    motivos_validos = ("ausente", "placeholder", "sin documentar")
    assert all(motivo in motivos_validos for _, motivo in hallazgos)
    # La que motivo todo esto ya esta cargada y no puede volver a faltar.
    assert ("TELEGRAM_ALLOWED_USER_ID", "ausente") not in hallazgos
    # Decididas el 2026-08-24. Se mira solo el nombre y no el motivo: da
    # igual si reaparecen como 'ausente', 'placeholder' o 'sin documentar',
    # las tres significan que la decision se deshizo.
    nombres = [name for name, _ in hallazgos]
    assert "STATE_DB_PATH" not in nombres
    assert "ALLOW_MOCK_DATA" not in nombres
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py::test_runs_against_the_real_repo_files -v
```

Esperado: FAIL en `assert "STATE_DB_PATH" not in nombres`.

Si sale `SKIPPED`, no hay `.env` local y esta tarea no se puede verificar:
parar y avisar, no seguir a ciegas.

- [ ] **Step 3: Comentar `STATE_DB_PATH` en `.env.example`**

Reemplazar las lineas 44-47 de `.env.example`:

```
# --- Estado local (SQLite) ---
# Ruta absoluta para el archivo de estado. Si no se define, usa ~/.macropipeline/state.db
# IMPORTANTE: no dejar en blanco si el scheduler puede ejecutar desde distintos directorios.
STATE_DB_PATH=/absolute/path/to/macropipeline/state.db
```

por:

```
# --- Estado local (SQLite) ---
# Opcional, y sin poner a proposito (decidido el 2026-08-24). Si no se
# define, usa ~/.macropipeline/state.db: absoluta y ajena al CWD, que es lo
# que cierra el bug de la base relativa al directorio de trabajo. ADR-007
# pide aparte que sea configurable, para poder apuntar a un fichero migrado
# si se cambia de maquina: por eso la variable existe y se declara aca
# aunque no se use. Definirla solo en ese caso.
# OJO: definirla en blanco (`STATE_DB_PATH=`) no es lo mismo que no
# definirla. sqlite3.connect("") abre una base temporal que se borra al
# cerrar, asi que la deduplicacion se resetea en cada run sin decir nada.
# STATE_DB_PATH=/absolute/path/to/macropipeline/state.db
```

El bloque `# --- Seguridad de datos ---` con `ALLOW_MOCK_DATA=false` se queda
exactamente como esta: esa si se exige en el `.env`.

- [ ] **Step 4: Correr el test y verificar que sigue fallando, pero por la otra**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py::test_runs_against_the_real_repo_files -v
```

Esperado: FAIL, ahora en `assert "ALLOW_MOCK_DATA" not in nombres`.
Que el fallo se haya movido es la prueba de que comentar la declaracion
silencia el chequeo sin tocar `scripts/check_publishers.py`.

- [ ] **Step 5: Agregar `ALLOW_MOCK_DATA` al `.env`**

`.env` tiene secretos: no imprimirlo entero ni pegarlo en la conversacion.
Insertar el bloque justo antes de la cabecera de Observability, para que el
orden espeje al del ejemplo:

```bash
.venv/Scripts/python.exe scripts/_insertar_allow_mock.py
```

donde `scripts/_insertar_allow_mock.py` es un fichero temporal con este
contenido, que se borra despues de correrlo (no se commitea):

```python
from pathlib import Path

env = Path(".env")
texto = env.read_text(encoding="utf-8")
ancla = "# --- Observability (Grafana Cloud) ---"
bloque = (
    "# --- Seguridad de datos ---\n"
    "# false en produccion: bloquea publicar con datos sinteticos si FMP y\n"
    "# Alpha Vantage fallan las dos. Explicita a proposito y no heredada del\n"
    "# default de orchestration/main.py: es una bandera de seguridad.\n"
    "ALLOW_MOCK_DATA=false\n\n"
)
assert "ALLOW_MOCK_DATA" not in texto, "ya estaba puesta, revisar a mano"
assert ancla in texto, "no se encontro la cabecera de Observability en .env"
env.write_text(texto.replace(ancla, bloque + ancla, 1), encoding="utf-8")
print("insertado")
```

Esperado: `insertado`. Despues:

```bash
rm scripts/_insertar_allow_mock.py
```

- [ ] **Step 6: Correr el test y verificar que pasa**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py::test_runs_against_the_real_repo_files -v
```

Esperado: PASS.

- [ ] **Step 7: Correr los tres gates enteros**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -q
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
.venv/Scripts/python.exe -m mypy src/ scripts/
```

Esperado: los cuatro en verde. `ruff format --check` es el que muerde aca: si
se queja del test, correr `ruff format tests/unit/test_check_publishers.py` y
repetir.

- [ ] **Step 8: Commit**

Solo dos ficheros. `.env` esta en `.gitignore:2` y no debe aparecer en el
`git status` de este commit — si aparece, parar: significa que se destrackeo
el ignore y hay secretos a punto de entrar al repo.

```bash
git status --short
git add tests/unit/test_check_publishers.py .env.example
git commit -m "chore(env): dejar STATE_DB_PATH al default y fijar ALLOW_MOCK_DATA"
```

---

### Task 2: borrar la base de datos huerfana de la raiz

**Files:**
- Delete: `macropipeline.db` (untracked, gitignoreado)

No lleva test: es un fichero fuera de git, no una linea de codigo.

- [ ] **Step 1: Mirar el contenido completo antes de borrar**

```bash
.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('macropipeline.db'); print([r[1] for r in c.execute('PRAGMA table_info(published_events)')]); print(c.execute('select * from published_events').fetchall())"
```

Esperado, exactamente esto:

```
['event_id', 'published_at', 'image_url']
[('weekly_close_2026-05-14', '2026-05-14 15:15:08.435020', 'r2://macropipeline-snapshots/weekly_close_2026-05-14.png')]
```

Tres columnas es el esquema pre-migracion, de codigo que ya no existe. Si
sale otra cosa —mas filas, o las columnas nuevas tipo `data_source` o
`status`— **parar y avisar**: seria estado escrito despues de la migracion, y
esta decision no lo contemplaba.

- [ ] **Step 2: Confirmar que git no lo trackea**

```bash
git ls-files --error-unmatch macropipeline.db
```

Esperado: `error: pathspec 'macropipeline.db' did not match any file(s) known to git`
(codigo de salida 1). Eso confirma que borrarlo no toca el historial.

- [ ] **Step 3: Borrar**

```bash
rm macropipeline.db
```

- [ ] **Step 4: Verificar que no quedo otra base suelta**

```bash
ls *.db 2>/dev/null || echo "sin .db en la raiz"
.venv/Scripts/python.exe -c "from pathlib import Path; d=Path.home()/'.macropipeline'; print('state.db real existe:', (d/'state.db').exists())"
```

Esperado: `sin .db en la raiz` y `state.db real existe: False`.

El `False` es correcto y no hay que arreglarlo: `StateDB` crea el fichero y su
directorio padre la primera vez que se instancia (`storage/state.py:31`). Que
no exista significa que no hubo ningun run completo en esta maquina desde la
migracion, no que falte configuracion.

- [ ] **Step 5: No hay commit**

Nada que commitear: el fichero no estaba en git. Confirmar que el arbol sigue
limpio:

```bash
git status --short
```

Esperado: sin salida.

---

### Task 3: verificacion de punta a punta

**Files:** ninguno

- [ ] **Step 1: Correr la comparacion de deriva sola, sin red**

`scripts/check_publishers.py` entero pega contra las APIs de X y LinkedIn.
Para verificar esta tarea alcanza con la parte que no sale a internet:

```bash
.venv/Scripts/python.exe -c "import importlib.util, pathlib; spec=importlib.util.spec_from_file_location('cp','scripts/check_publishers.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.compare_env_files(pathlib.Path('.env.example'), pathlib.Path('.env')))"
```

Esperado: `[]`.

- [ ] **Step 2: Correr el script completo y mirar la seccion de deriva**

Este si sale a internet (dos GET de solo lectura, no publica nada).

```bash
.venv/Scripts/python.exe scripts/check_publishers.py
```

Esperado en la seccion `-- .env vs .env.example ---`:

```
[ OK ] Sin deriva entre .env y .env.example.
```

El resto de la salida (X, LinkedIn, el codigo de salida) es ajeno a esta
tarea. Si X o LinkedIn salen en rojo, es el punto 7 del backlog —el token de
LinkedIn vence alrededor del 2026-10-20— y no algo que este plan haya roto.

---

### Task 4: actualizar la memoria del proyecto

**Files:**
- Modify: `C:/Users/ASUS/.claude/projects/C--Users-ASUS-Documents-GitHub-MacroPipeline/memory/macropipeline-pending-work.md`
- Modify: `C:/Users/ASUS/.claude/projects/C--Users-ASUS-Documents-GitHub-MacroPipeline/memory/MEMORY.md`

- [ ] **Step 1: Cerrar el punto 5 y corregir el hecho falso**

El punto 5 dice hoy: *"mientras el estado real vive en `~/.macropipeline/state.db`,
justamente porque `STATE_DB_PATH` no esta"*. Las dos mitades son falsas y hay
que borrarlas, no matizarlas: ese directorio no existe, y el fichero de la raiz
es esquema pre-migracion, o sea que lo dejo el codigo viejo con `db_path`
relativo al CWD, no la falta de `STATE_DB_PATH`.

Reemplazar el punto 5 entero por:

```markdown
5. ~~Decidir `STATE_DB_PATH` y `ALLOW_MOCK_DATA`.~~ **Cerrado el 2026-08-24.**
   Las dos corrian con el default del codigo y **los dos defaults ya eran el
   valor correcto**, asi que escribirlas en el `.env` no cambiaba ni una linea
   de comportamiento. No era una decision de configuracion sino sobre el
   vocabulario del chequeo de deriva. `STATE_DB_PATH` quedo **declarada
   comentada** en `.env.example` (entra en `documentadas` via
   `_commented_names()` sin entrar en `ejemplo`, igual que
   `LINKEDIN_TOKEN_ISSUED`); `ALLOW_MOCK_DATA=false` va **explicita en el
   `.env`** porque decide si se puede publicar con datos sinteticos y una
   bandera de seguridad no deberia depender de un default del codigo.
   Cero cambios en `scripts/check_publishers.py`.
   Tres hechos que costaron verificar y conviene no volver a descubrir:
   **`~/.macropipeline/state.db` nunca existio** (ningun run completo en esta
   maquina desde la migracion); el `macropipeline.db` de la raiz —ya
   borrado— tenia el esquema pre-migracion de tres columnas, o sea que lo
   dejo el codigo viejo con `db_path` relativo al CWD y **no** la falta de
   `STATE_DB_PATH`; y `STATE_DB_PATH=` en blanco no equivale a no definirla,
   porque `sqlite3.connect("")` abre una base temporal que se borra al
   cerrar y la deduplicacion se resetearia en cada run en silencio.
   Spec en `docs/superpowers/specs/2026-08-24-state-db-path-allow-mock-data-design.md`.
```

- [ ] **Step 2: Abrir el punto nuevo sobre la persistencia en produccion**

Agregar al final de la lista de puntos abiertos:

```markdown
10. **La idempotencia que promete ADR-002 no se sostiene si Routines corre en
    un entorno efimero.** El ADR dice que "la segunda ejecucion detecta el
    `event_id` ya publicado en SQLite", pero tambien dice que Routines clona
    el repo y ejecuta en un entorno gestionado por Anthropic. Si ese entorno
    es efimero, el fichero SQLite no sobrevive entre runs **con ninguna ruta**,
    y `is_published()` devuelve siempre False. Encontrado el 2026-08-24
    decidiendo `STATE_DB_PATH`. Hoy no muerde: **nada corre
    `orchestration/main.py` en un schedule** —el unico cron del repo es el
    nightly de contract tests y no hay config de Routines versionada—, asi que
    es un problema sin construir, no uno roto. Verificar primero si Routines
    persiste el workspace entre runs; si no, el estado tiene que salir del
    disco local (R2 ya esta configurado y es el candidato obvio) antes de que
    el pipeline corra desatendido.
```

- [ ] **Step 3: Actualizar la linea del indice**

En `MEMORY.md`, la linea de `macropipeline-pending-work.md` dice que el
primer pendiente es decidir `STATE_DB_PATH`. Reemplazarla por:

```markdown
- [Backlog abierto de MacroPipeline](macropipeline-pending-work.md) — pendientes al 2026-08-24; cinco puntos cerrados ese dia (verificacion LLM, secrets, alerta de rechazo, deriva de .env, STATE_DB_PATH/ALLOW_MOCK_DATA); ahora el primero son los contract tests de FMP/AV, y quedo abierto que el estado SQLite no sobrevive a un entorno efimero de Routines
```

- [ ] **Step 4: Sin commit**

La memoria vive fuera del repo. Confirmar que el arbol de git sigue limpio:

```bash
git status --short
```

Esperado: sin salida.

---

## Como saber que termino

Las cuatro, todas verificadas y no asumidas:

1. `compare_env_files(.env.example, .env)` devuelve `[]`.
2. `python scripts/check_publishers.py` imprime `[ OK ] Sin deriva entre .env y .env.example.`
3. `pytest tests/unit/ -q`, `ruff check`, `ruff format --check` y `mypy` en verde sobre `src/ tests/ scripts/`.
4. No hay ningun `.db` en la raiz del repo, y `git status --short` no devuelve nada.
