# Un switch por componente — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que ningún componente con credenciales pueda matar `MacroOrchestrator.__init__`, y que todo lo que quedó roto o apagado al arrancar se reporte desde un punto de decisión único dentro de `run_weekly_close`, donde ya existen el canal de aviso y el `event_id`.

**Architecture:** Se generaliza el par `publisher_enabled` / `build_publisher` —hoy sólo para X y LinkedIn— a los ocho componentes con credenciales, en un módulo nuevo `macro_pipeline/components.py`. El constructor pasa a ser total: guarda motivos en dos diccionarios (`switch_errors` para intención ilegible, `component_errors` para credencial ausente) y no levanta. `run_weekly_close` gana un punto de decisión que absorbe la guarda de publicadores actual, y devuelve un código de salida.

**Tech Stack:** Python 3.12, structlog, pytest, ruff, mypy `--strict`. **Todo se corre con `./.venv/Scripts/python.exe -m ...`** — el Python global de la máquina no tiene `anthropic`, `playwright` ni `boto3`, ni el paquete en editable, y produce fallos y errores de tipo que no son reales.

**Spec:** `docs/superpowers/specs/2026-08-29-component-switches-design.md`

---

## Estructura de ficheros

| Fichero | Responsabilidad | Acción |
|---|---|---|
| `src/macro_pipeline/components.py` | Leer switches y construir componentes con tres estados de retorno | Crear (mueve `publishers/flags.py`) |
| `src/macro_pipeline/publishers/flags.py` | — | Borrar |
| `src/macro_pipeline/orchestration/main.py` | Constructor total, punto de decisión, código de salida | Modificar |
| `scripts/check_publishers.py` | Verificar credenciales de publicación | Modificar (sólo el import) |
| `.env.example` | Declarar las variables | Modificar |
| `docs/adr/009-degradation-policy.md` | La política | Modificar |
| `tests/unit/test_components.py` | Switches y construcción tri-estado | Crear (mueve `test_publisher_flags.py`) |
| `tests/unit/test_orchestrator_startup.py` | Constructor total | Crear |
| `tests/integration/test_orchestrator_startup_gate.py` | Punto de decisión con `StateDB` real | Crear |

---

## Task 1: El módulo `components.py`

Mover `publishers/flags.py` a la raíz del paquete, renombrar las dos funciones y añadir `read_switch`. Nada de comportamiento cambia todavía.

**Files:**
- Create: `src/macro_pipeline/components.py`
- Delete: `src/macro_pipeline/publishers/flags.py`
- Modify: `src/macro_pipeline/orchestration/main.py` (imports), `scripts/check_publishers.py:26-29,263-264`
- Test: `tests/unit/test_components.py` (mueve `tests/unit/test_publisher_flags.py`)

- [ ] **Step 1: Mover los dos ficheros con git, para conservar la historia**

```bash
git mv src/macro_pipeline/publishers/flags.py src/macro_pipeline/components.py
git mv tests/unit/test_publisher_flags.py tests/unit/test_components.py
```

- [ ] **Step 2: Escribir los tests nuevos de `read_switch`**

Añadir al final de `tests/unit/test_components.py`:

```python
def test_read_switch_reports_an_invalid_value_instead_of_raising(monkeypatch):
    """El orquestador no puede dejar que esto levante: seria el bug (d) otra vez.

    `component_enabled` sigue levantando porque `check_publishers.py` lo quiere
    asi. `read_switch` es la version que devuelve el motivo para que el
    constructor pueda seguir y el punto de decision lo reporte.
    """
    monkeypatch.setenv(USE_FRED_VAR, "maybe")

    encendido, motivo = read_switch(USE_FRED_VAR)

    assert encendido is False
    assert motivo is not None
    assert "maybe" in motivo


def test_read_switch_has_no_motive_when_the_value_is_valid(monkeypatch):
    monkeypatch.setenv(USE_FRED_VAR, "false")
    assert read_switch(USE_FRED_VAR) == (False, None)


def test_an_invalid_switch_reads_as_off_which_is_why_the_motive_matters(monkeypatch):
    """La trampa que este par de valores esconde.

    Un valor invalido devuelve `False`, asi que el componente no se construye y
    queda **indistinguible de un apagado deliberado**. Lo unico que los separa
    es el motivo. Por eso el punto de decision mira `switch_errors` antes que
    cualquier rama de apagado.
    """
    monkeypatch.setenv(USE_TELEGRAM_VAR, "maybe")
    encendido, motivo = read_switch(USE_TELEGRAM_VAR)
    assert encendido is False
    assert motivo is not None
```

Y cambiar el bloque de imports del fichero (líneas 11-16) por:

```python
from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_FRED_VAR,
    USE_TELEGRAM_VAR,
    build_component,
    component_enabled,
    read_switch,
)
```

- [ ] **Step 3: Renombrar los usos dentro del fichero de tests**

```bash
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("tests/unit/test_components.py")
s = p.read_text(encoding="utf-8")
s = s.replace("publisher_enabled(", "component_enabled(")
s = s.replace("build_publisher(", "build_component(")
p.write_text(s, encoding="utf-8")
PY
```

- [ ] **Step 4: Correr los tests para verlos fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_components.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'macro_pipeline.components'` no; el fichero ya existe por el `git mv`, así que el error real es `ImportError: cannot import name 'read_switch'`.

- [ ] **Step 5: Escribir el módulo**

Reemplazar la cabecera y las dos funciones de `src/macro_pipeline/components.py` por:

```python
"""Switches de encendido/apagado por componente.

Vive en la raiz del paquete y no bajo `publishers/` por dos motivos: lo usan
dos consumidores que no se importan entre si —el orquestador y
`scripts/check_publishers.py`, que no puede arrastrar pandas y los siete
clientes—, y desde ADR-009 cubre los ocho componentes con credenciales y no
solo las dos redes.
"""

import os
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

USE_FMP_VAR = "USE_FMP"
USE_AV_VAR = "USE_AV"
USE_FRED_VAR = "USE_FRED"
USE_ANTHROPIC_VAR = "USE_ANTHROPIC"
USE_R2_VAR = "USE_R2"
USE_TELEGRAM_VAR = "USE_TELEGRAM"
PUBLISH_X_VAR = "PUBLISH_X"
PUBLISH_LINKEDIN_VAR = "PUBLISH_LINKEDIN"


def component_enabled(var: str) -> bool:
    """`True`/`False` desde una variable de entorno, o levanta.

    Ausente o vacia -> True: el default es participar, y eso es lo que hace que
    un `.env` sin copiar deje todo encendido y por lo tanto todo alertando, en
    vez de callarse.

    Cualquier valor que no sea `true` o `false` levanta `ValueError` a
    proposito, mostrando lo que el operador realmente tipeo. Las dos formas de
    adivinar son silenciosas y las dos hacen dano.
    """
    original = os.environ.get(var, "")
    raw = original.strip().lower()
    if not raw:
        return True
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(
        f"{var}={original!r} no es un valor valido: se espera 'true' o 'false'."
    )


def read_switch(var: str) -> tuple[bool, str | None]:
    """`(encendido, motivo)`: la version que no levanta.

    Un motivo distinto de `None` significa que **no se pudo leer la intencion
    del operador**, que no es lo mismo que una credencial ausente: degradar
    seria adivinar. Devuelve `False` en ese caso, asi que el componente no se
    construye y queda indistinguible de un apagado deliberado — por eso el
    motivo es lo unico que los separa, y por eso el punto de decision mira los
    `switch_errors` antes que ninguna rama de apagado.

    `component_enabled` sigue existiendo y levantando porque
    `scripts/check_publishers.py` lo quiere asi: ahi un valor invalido debe
    romper el chequeo.
    """
    try:
        return component_enabled(var), None
    except ValueError as e:
        return False, str(e)


def build_component[T](
    name: str, factory: Callable[[], T], enabled: bool
) -> tuple[T | None, str | None]:
    """Construye un componente, devolviendo (cliente, motivo).

    Las tres combinaciones son distintas y el orquestador las distingue:

    - `(cliente, None)` — listo.
    - `(None, None)` — apagado a proposito: se loggea y nada mas. **No alerta**:
      una decision tuya no es un fallo, y alertar cada semana por una pausa
      deliberada es lo que hace que se dejen de leer las alertas.
    - `(None, motivo)` — encendido y roto: falta alguna credencial. El motivo es
      el texto del `ValueError` del cliente y termina en la alerta de Telegram.

    Solo se atrapa `ValueError`, que es lo que levantan los clientes cuando
    falta una credencial. Cualquier otra excepcion sale y mata la run: no es un
    componente roto, es un bug.
    """
    if not enabled:
        logger.info("component_disabled", component=name)
        return None, None
    try:
        return factory(), None
    except ValueError as e:
        logger.warning("component_not_configured", component=name, reason=str(e))
        return None, str(e)
```

- [ ] **Step 6: Actualizar los dos consumidores**

En `src/macro_pipeline/orchestration/main.py`, reemplazar el bloque de import:

```python
from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    build_publisher,
    publisher_enabled,
)
```

por:

```python
from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    build_component,
    component_enabled,
)
```

y cambiar las tres llamadas de `main.py:144-149`:

```python
        x_enabled = component_enabled(PUBLISH_X_VAR)
        linkedin_enabled = component_enabled(PUBLISH_LINKEDIN_VAR)
        self.x_client, self.x_error = build_component("x", XClient, x_enabled)
        self.linkedin, self.linkedin_error = build_component(
            "linkedin", LinkedInClient, linkedin_enabled
        )
```

En `scripts/check_publishers.py`, cambiar el import de `macro_pipeline.publishers.flags` a `macro_pipeline.components` y las dos llamadas de las líneas 263-264 de `publisher_enabled` a `component_enabled`.

- [ ] **Step 7: Correr la suite entera**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS, 176 tests (173 de antes + 3 nuevos de `read_switch`).

- [ ] **Step 8: Gates**

Run: `./.venv/Scripts/python.exe -m ruff format src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m mypy src/ scripts/`
Expected: `All checks passed!` y `Success: no issues found`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(components): flags.py sale de publishers y gana read_switch

Mismo comportamiento: `component_enabled` y `build_component` son las de antes
con otro nombre. Lo nuevo es `read_switch`, la version que devuelve el motivo en
vez de levantar, que es lo que permite que el constructor deje de morir.

El modulo sube a la raiz del paquete porque desde ADR-009 cubre los ocho
componentes con credenciales, no solo las dos redes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Constructor total

Los ocho componentes pasan por `_build`, que anota motivos en vez de dejar que la excepción salga. `x_error`, `linkedin_error` y `r2_ready` pasan a ser propiedades derivadas, por el mismo motivo que ya lo son `x_ready` y `linkedin_ready`: un atributo se puede desincronizar del cliente, y un test lo puede poner a mano para saltarse el mockeo — que es como el bug de `5ba7997` vivió detrás de cuatro tests verdes.

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:84-152`
- Modify: `tests/integration/test_orchestrator_exit_states.py:50-55`, `tests/integration/test_orchestrator_persistence.py:41,228`
- Test: `tests/unit/test_orchestrator_startup.py` (nuevo)

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/unit/test_orchestrator_startup.py`:

```python
"""El constructor no puede morir por una credencial ausente.

Limitacion (d) de ADR-009: hasta hoy `FMPClient`, `AlphaVantageClient`,
`TelegramBot` y las dos banderas levantaban `ValueError` sin que nadie lo
atrapara, asi que la run moria antes de `run_weekly_close` — sin alerta, sin
fila de estado, y repitiendose igual la semana siguiente.

Estos tests no comprueban que la run haga algo util sin credenciales: solo que
el constructor sobreviva y deje escrito el motivo. Que hacer con el motivo es
del punto de decision, y se prueba en
`tests/integration/test_orchestrator_startup_gate.py`.
"""

import pytest

from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FMP_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
)
from macro_pipeline.orchestration.main import MacroOrchestrator

# (componente, variables de entorno que hay que borrar para romperlo)
COMPONENTES = [
    ("fmp", ["FMP_API_KEY"]),
    ("av", ["ALPHA_VANTAGE_API_KEY"]),
    ("fred", ["FRED_API_KEY"]),
    ("anthropic", ["ANTHROPIC_API_KEY"]),
    ("r2", ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]),
    ("telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    ("x", ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]),
    ("linkedin", ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"]),
]

TODAS_LAS_CREDENCIALES = [v for _, variables in COMPONENTES for v in variables]

SWITCHES = [
    USE_FMP_VAR,
    USE_AV_VAR,
    USE_FRED_VAR,
    USE_ANTHROPIC_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
    PUBLISH_X_VAR,
    PUBLISH_LINKEDIN_VAR,
]


@pytest.fixture
def entorno_completo(monkeypatch, tmp_path):
    """Todas las credenciales presentes y ninguna bandera puesta.

    El `.env` del desarrollador se cuela en los unit tests —
    `tests/contract/conftest.py` hace `load_dotenv` al importarse aunque los
    contract tests esten deseleccionados—, asi que aca se fija el entorno
    entero a mano en vez de confiar en lo que haya.
    """
    for var in TODAS_LAS_CREDENCIALES:
        monkeypatch.setenv(var, "valor-de-prueba")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    for var in SWITCHES:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("componente,variables", COMPONENTES)
def test_a_missing_credential_does_not_kill_the_constructor(
    componente, variables, entorno_completo, monkeypatch
):
    for var in variables:
        monkeypatch.delenv(var, raising=False)

    orch = MacroOrchestrator()

    assert componente in orch.component_errors
    assert orch.component_errors[componente]
    assert orch.switch_errors == {}


def test_an_invalid_switch_does_not_kill_the_constructor_either(
    entorno_completo, monkeypatch
):
    """La otra mitad de (d): un `PUBLISH_X=yes` mataba la run igual de callado."""
    monkeypatch.setenv(PUBLISH_X_VAR, "yes")

    orch = MacroOrchestrator()

    assert PUBLISH_X_VAR in orch.switch_errors
    assert "yes" in orch.switch_errors[PUBLISH_X_VAR]


def test_a_deliberate_switch_off_leaves_no_motive(entorno_completo, monkeypatch):
    """Apagado no es roto: sin motivo no hay nada que alertar."""
    monkeypatch.setenv(USE_FRED_VAR, "false")

    orch = MacroOrchestrator()

    assert orch.fred is None
    assert "fred" not in orch.component_errors


def test_everything_configured_leaves_both_dicts_empty(entorno_completo):
    orch = MacroOrchestrator()

    assert orch.component_errors == {}
    assert orch.switch_errors == {}
    assert orch.r2_ready is True
    assert orch.x_ready is True
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_startup.py -q`
Expected: FAIL — `AttributeError: 'MacroOrchestrator' object has no attribute 'component_errors'`, y varios casos con `ValueError` saliendo del constructor.

- [ ] **Step 3: Escribir el constructor**

Reemplazar de `main.py:84` (la línea `def __init__`) hasta `main.py:152` (la línea de `self._allow_mock`) por:

```python
    def __init__(self, tracer: trace.Tracer | None = None) -> None:
        self.tracer = tracer
        logger.info("initializing_orchestrator")

        # Ningun componente con credenciales puede matar el constructor
        # (ADR-009, limitacion (d)). Los motivos se juntan aca y los reporta el
        # punto de decision de `run_weekly_close`, que es el primer sitio donde
        # existen a la vez el canal de aviso y el `event_id`. Con eso el orden
        # de construccion deja de ser logica: era la raiz del problema —FMP y
        # AV se construian antes que Telegram, asi que cuando reventaban no
        # habia con que avisar— y no solo su sintoma.
        #
        # Dos diccionarios y no uno: un switch con un valor invalido significa
        # que no se pudo leer la intencion del operador y eso aborta siempre;
        # una credencial ausente degrada o aborta segun el componente. Unirlos
        # obligaria a llevar una bandera al lado de cada motivo.
        self.switch_errors: dict[str, str] = {}
        self.component_errors: dict[str, str] = {}

        self.fmp = self._build("fmp", USE_FMP_VAR, FMPClient)
        self.av = self._build("av", USE_AV_VAR, AlphaVantageClient)
        self.fred = self._build("fred", USE_FRED_VAR, FREDClient)
        self.llm = self._build("anthropic", USE_ANTHROPIC_VAR, LLMClient)
        self.r2 = self._build("r2", USE_R2_VAR, R2Client)
        self.telegram = self._build("telegram", USE_TELEGRAM_VAR, TelegramBot)
        self.x_client = self._build("x", PUBLISH_X_VAR, XClient)
        self.linkedin = self._build("linkedin", PUBLISH_LINKEDIN_VAR, LinkedInClient)

        # `ValidatorAgent.__init__` solo guarda el cliente, asi que no puede
        # fallar por credenciales: sin generador no hay validador, y con
        # generador siempre se construye.
        self.validator_agent = (
            ValidatorAgent(self.llm) if self.llm is not None else None
        )

        # Motivo por el que el bloque macro no llego, o None. Es de
        # **ejecucion** —API caida, serie corta, dato rancio— y por eso no vive
        # en `component_errors`, que es de arranque. Los dos alertan, pero en
        # sitios distintos y con textos distintos.
        self.macro_error: str | None = None

        self.validator_engine = ValidationEngine()
        self.renderer = PlaywrightEngine()
        self.state = StateDB()

        # Guardia de Mock Data: por defecto bloqueado en producción
        self._allow_mock = os.environ.get("ALLOW_MOCK_DATA", "false").lower() == "true"

    def _build[T](self, name: str, var: str, factory: Callable[[], T]) -> T | None:
        """Lee el switch, construye si corresponde y anota el motivo si falla."""
        enabled, switch_error = read_switch(var)
        if switch_error is not None:
            self.switch_errors[var] = switch_error
            return None
        cliente, motivo = build_component(name, factory, enabled)
        if motivo is not None:
            self.component_errors[name] = motivo
        return cliente
```

- [ ] **Step 4: Añadir las propiedades derivadas y ajustar los imports**

Justo después de `linkedin_ready`, añadir:

```python
    @property
    def r2_ready(self) -> bool:
        return self.r2 is not None

    @property
    def x_error(self) -> str | None:
        """Derivada de `component_errors` y no un atributo aparte.

        Mismo motivo que `x_ready`: una sola fuente de verdad, para que la
        alerta no pueda nombrar una red distinta de la que se rompio.
        """
        return self.component_errors.get("x")

    @property
    def linkedin_error(self) -> str | None:
        return self.component_errors.get("linkedin")
```

Y en el bloque de imports de `main.py`, añadir `from collections.abc import Callable` (arriba, junto a `from contextlib import nullcontext`) y ampliar el import de `macro_pipeline.components`:

```python
from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FMP_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
    build_component,
    read_switch,
)
```

`component_enabled` deja de importarse en `main.py` — lo sigue usando sólo `check_publishers.py`.

- [ ] **Step 5: Ajustar los fixtures que asignaban los atributos que ahora son propiedades**

En `tests/integration/test_orchestrator_exit_states.py`, reemplazar las líneas 50-55:

```python
    orch.r2_ready = False
    ...
    orch.x_error = None
    orch.linkedin_error = None
    orch.macro_error = None
```

por:

```python
    orch.r2 = None
    orch.switch_errors = {}
    orch.component_errors = {}
    orch.macro_error = None
```

En `tests/integration/test_orchestrator_persistence.py`, cambiar la línea 41 (`orch.r2_ready = False`) por `orch.r2 = None` y la 228 (`orch.r2_ready = True`) por `orch.r2 = MagicMock()`, y añadir `orch.switch_errors = {}` y `orch.component_errors = {}` en el fixture de construcción.

- [ ] **Step 6: Correr la suite entera**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS. Si algún test falla con `AttributeError: property 'r2_ready' of 'MacroOrchestrator' object has no setter`, es un fixture que quedó sin ajustar: buscarlo con `grep -rn "r2_ready = \|x_error = \|linkedin_error = " tests/`.

- [ ] **Step 7: Gates y commit**

```bash
./.venv/Scripts/python.exe -m ruff format src/ tests/ scripts/
./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
./.venv/Scripts/python.exe -m mypy src/ scripts/
git add -A
git commit -m "feat(orchestration): el constructor deja de morir por una credencial

Los ocho componentes con credenciales pasan por `_build`, que anota el motivo en
`component_errors` (credencial ausente) o en `switch_errors` (valor de switch
invalido) en vez de dejar salir el `ValueError`. Nadie reporta todavia: eso es
del punto de decision.

`r2_ready`, `x_error` y `linkedin_error` pasan a ser propiedades derivadas por
el mismo motivo que ya lo eran `x_ready` y `linkedin_ready`: un atributo se
desincroniza del cliente, y un test lo puede poner a mano para saltearse el
mockeo — que es como vivio el bug de \`5ba7997\` detras de cuatro tests verdes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: El punto de decisión y el código de salida

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py` — nuevo `_startup_exit_code`, sustituye la guarda de `main.py:336-356`, `run_weekly_close -> int`, bloque `__main__`
- Test: `tests/integration/test_orchestrator_startup_gate.py` (nuevo)

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/integration/test_orchestrator_startup_gate.py`:

```python
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


def test_a_broken_fmp_aborts_with_the_real_cause_and_leaves_no_row(data, state):
    """FMP no tiene ruta viva: la de AV no publica (ADR-009, divergencia 4).

    Lo que importa tanto como el abort es **donde**: antes del lock, asi que no
    queda fila y la proxima run reintenta sola.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors["fmp"] = "Se requiere FMP_API_KEY en el entorno."

    assert orch.run_weekly_close() == 1

    orch.telegram.send_alert.assert_called_once()
    assert "FMP_API_KEY" in orch.telegram.send_alert.call_args[0][0]
    assert state.get_publication_state(EVENT_ID) == {}


def test_fmp_switched_off_aborts_in_silence(data, state):
    """Apagar la unica fuente con ruta viva impide publicar: es un abort.

    Deliberado, asi que no alerta — y `0`, porque nadie se equivoco.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None

    assert orch.run_weekly_close() == 0

    orch.telegram.send_alert.assert_not_called()
    assert state.get_publication_state(EVENT_ID) == {}


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
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_startup_gate.py -q`
Expected: FAIL — `AttributeError: 'MacroOrchestrator' object has no attribute '_startup_exit_code'` y `assert None == 1`, porque `run_weekly_close` todavía devuelve `None`.

- [ ] **Step 3: Añadir la tabla de consecuencias**

Justo debajo de `_PROMPT_VERSION` en `main.py`, añadir:

```python
# Que le pasa al cierre cuando cada componente esta encendido y sin
# credenciales. Viaja a la alerta, asi que dice la consecuencia y no el nombre
# interno: quien la lee tiene que poder decidir si aprueba sin abrir el codigo.
# FMP y Telegram no estan porque no degradan — abortan, y cada uno con su texto.
_CONSECUENCIA = {
    "av": "sin fallback si FMP falla, y hoy esa ruta tampoco publicaría",
    "fred": "el cierre sale sin bloque macro",
    "anthropic": "el cierre sale con el titular genérico",
    "r2": "sin copia remota de la imagen",
    "x": "el cierre sale solo en LinkedIn",
    "linkedin": "el cierre sale solo en X",
}
```

- [ ] **Step 4: Escribir el punto de decisión**

Añadir el método justo antes de `run_weekly_close`:

```python
    def _startup_exit_code(self, event_id: str) -> int | None:
        """Reporta lo que dejo el constructor y decide si la run sigue.

        Devuelve el codigo de salida si no sigue, o `None` si sigue.

        **El orden de las dos primeras ramas es obligatorio, no estetico.**
        `read_switch` devuelve `(False, motivo)` ante un valor invalido, asi que
        con `USE_TELEGRAM=maybe` el cliente no se construye y queda
        indistinguible de un apagado deliberado. Con la rama del apagado
        primero, ese caso saldria con `0` y en silencio: el mismo agujero
        invisible que este metodo existe para cerrar, reintroducido por el orden
        de dos `if`.
        """
        # ── 1. Intencion ilegible: nada se puede decidir sin ella ──────────
        if self.switch_errors:
            detalle = "\n".join(f"• {m}" for m in sorted(self.switch_errors.values()))
            if self.telegram is None:
                logger.error(
                    "switch_invalid_no_channel_aborting",
                    event_id=event_id,
                    detail=detalle,
                )
                return 1
            logger.error("switch_invalid_aborting", event_id=event_id)
            self.telegram.send_alert(
                "⛔ El cierre semanal no se ejecutó: hay switches con un valor "
                "que no es `true` ni `false`, así que no se puede saber qué "
                f"debía correr.\n\n{detalle}\n\n"
                "No se publicó nada y el evento queda sin marcar: la próxima "
                "run lo reintenta."
            )
            return 1

        # ── 2. Pausa deliberada del pipeline entero ────────────────────────
        # `_build` deja motivo solo cuando el componente estaba encendido y
        # fallo, asi que su ausencia es la unica fuente de verdad de «apagado a
        # proposito».
        if self.telegram is None and "telegram" not in self.component_errors:
            logger.info("pipeline_paused_telegram_disabled", event_id=event_id)
            return 0

        # ── 3. Sin canal y sin HITL: el caso irreducible ───────────────────
        if self.telegram is None:
            logger.error(
                "telegram_unavailable_aborting",
                event_id=event_id,
                detail="; ".join(
                    f"{c}: {m}" for c, m in sorted(self.component_errors.items())
                ),
            )
            return 1

        # ── 4. Abortos: lo que impide publicar ─────────────────────────────
        if self.fmp is None:
            if "fmp" not in self.component_errors:
                logger.info("pipeline_paused_fmp_disabled", event_id=event_id)
                return 0
            logger.error("data_source_unavailable_aborting", event_id=event_id)
            self.telegram.send_alert(
                "⛔ El cierre semanal no se ejecutó: FMP es la única fuente con "
                "una ruta capaz de publicar.\n\n"
                f"Motivo: {self.component_errors['fmp']}\n\n"
                "La ruta de Alpha Vantage no publica: pide `SPY` donde FMP pide "
                "`^GSPC`, y el validador la rechaza por rango (ADR-009, "
                "divergencia 4).\n\n"
                "No se publicó nada y el evento queda sin marcar: la próxima "
                "run lo reintenta."
            )
            return 1

        if not (self.x_ready or self.linkedin_ready):
            fallos = self._publisher_failures()
            if not fallos:
                logger.info("no_publishers_enabled", event_id=event_id)
                return 0
            logger.error("publishers_not_ready_aborting", event_id=event_id)
            self.telegram.send_alert(
                "⚠️ El cierre semanal no se ejecutó: no hay ninguna red en "
                "condiciones de publicar.\n\n"
                f"{fallos}\n\n"
                "No se publicó nada y el evento queda sin marcar, así que la "
                "próxima run lo reintenta. Verificar con "
                "`python scripts/check_publishers.py`."
            )
            return 1

        # ── 5. Degradaciones de arranque: una sola alerta ──────────────────
        degradaciones = {
            c: m
            for c, m in self.component_errors.items()
            if c not in ("fmp", "telegram")
        }
        if degradaciones:
            logger.warning(
                "startup_degraded", event_id=event_id, components=sorted(degradaciones)
            )
            lineas = "\n".join(
                f"• {c}: {m} — {_CONSECUENCIA[c]}"
                for c, m in sorted(degradaciones.items())
            )
            self.telegram.send_alert(
                "⚠️ El cierre semanal arranca con componentes encendidos y sin "
                f"credenciales:\n\n{lineas}\n\n"
                "Se publica igual si lo aprobás. Verificar con "
                "`python scripts/check_publishers.py`."
            )
        return None
```

- [ ] **Step 5: Sustituir la guarda de publicadores y ligar el local de Telegram**

Reemplazar todo el bloque de `main.py:317-356` —desde el comentario `# ── Sin ninguna red no hay cierre semanal ──` hasta el `return` que lo cierra— por:

```python
                # ── Punto de decisión: lo que el constructor dejó roto ─────
                # Va después del guard de duplicados (si ese cierre ya salió no
                # hay nada que reportar) y antes del lock (un abort acá no deja
                # fila, así que la próxima run reintenta sola).
                code = self._startup_exit_code(event_id)
                if code is not None:
                    return code

                # El punto de decisión ya abortó si Telegram no está. El local
                # es lo que se lo dice a `mypy` —el narrowing de un atributo no
                # sobrevive a las llamadas a `self` de más abajo— y de paso hace
                # que las cinco llamadas siguientes no dependan de que nadie
                # reordene las ramas de arriba.
                telegram = self.telegram
                if telegram is None:
                    raise RuntimeError(
                        "Telegram ausente después del punto de decisión: alguna "
                        "rama dejó pasar una run sin canal."
                    )
```

- [ ] **Step 6: Cambiar la firma, los `return` y las llamadas a Telegram**

1. `def run_weekly_close(self) -> None:` → `def run_weekly_close(self) -> int:`, y ampliar el docstring: `"""Pipeline completo de Cierre Semanal con idempotencia parcial.\n\n        Devuelve el código de salida: `0` si la run corrió —publicó, o\n        deliberadamente no publicó—, `1` si abortó por configuración rota. Las\n        excepciones inesperadas siguen propagando: salida controlada → entero,\n        bug → excepción.\n        """`
2. Todos los `return` sueltos que quedan dentro del método pasan a `return 0`: el del guard de duplicados, el de `pipeline_already_running_skipping` y el del timeout de Telegram. **Ninguno pasa a `1`**: el `1` está reservado para el abort que no deja fila, que es el único que necesita rastro fuera del proceso.
3. Al final del método, después del `else` que marca `rejected_by_human`, añadir `return 0` como última sentencia del `with`.
4. **Borrar el bloque de `publisher_degraded`** —el `if self.x_error or self.linkedin_error:` con su `logger.warning` y su `send_alert`, unas veinte líneas justo antes del bloque de `macro_degraded`—. Su causa siempre es de arranque, así que ahora sale del punto de decisión y dejarlo haría sonar dos alertas por lo mismo. El bloque de `macro_degraded` que le sigue **no se toca**: su causa es de ejecución.
5. Dentro de `run_weekly_close`, reemplazar `self.telegram.` por `telegram.` en las llamadas que quedan: la alerta de degradación de la capa LLM, la del bloque macro, `send_approval_request`, `wait_for_approval` y la alerta de R2.

- [ ] **Step 7: Actualizar el bloque `__main__`**

```python
if __name__ == "__main__":
    import logging
    import sys

    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(format="%(message)s", level=logging.INFO)

    tracer = setup_observability()
    orchestrator = MacroOrchestrator(tracer=tracer)
    sys.exit(orchestrator.run_weekly_close())
```

- [ ] **Step 8: Correr los tests nuevos y luego la suite entera**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_startup_gate.py -q`
Expected: PASS, 11 tests.

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS. Si falla algo de `test_orchestrator_exit_states.py` o `test_orchestrator_persistence.py` por `publisher_degraded`, es el test de la alerta de publicadores que ahora sale del punto de decisión: mover su aserción a que `send_alert` se llame una vez con el texto nuevo.

- [ ] **Step 9: Las mutaciones que justifican los tests**

Aplicar cada una, correr la suite, comprobar que cae **exactamente** el test nombrado, y revertir:

| Mutación | Tiene que caer |
|---|---|
| Mover la rama 2 (Telegram apagado) delante de la 1 (`switch_errors`) | `test_an_invalid_telegram_switch_is_not_read_as_a_deliberate_pause` |
| Poner `"fmp"` dentro del dict `degradaciones` en vez de excluirlo | `test_a_broken_fmp_aborts_with_the_real_cause_and_leaves_no_row` |
| Cambiar el texto compuesto de la rama 5 por uno fijo | `test_two_startup_degradations_produce_one_alert_naming_both` |
| Mover el punto de decisión delante del guard de duplicados | `test_the_gate_runs_after_the_duplicate_guard` |
| Devolver el bloque de `publisher_degraded` a su sitio | `test_one_broken_publisher_alerts_once_and_not_twice` |

Si alguna mutación deja la suite verde, el test correspondiente no está mirando lo que dice mirar y hay que arreglarlo antes de seguir.

- [ ] **Step 10: Gates y commit**

```bash
./.venv/Scripts/python.exe -m ruff format src/ tests/ scripts/
./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
./.venv/Scripts/python.exe -m mypy src/ scripts/
git add -A
git commit -m "feat(orchestration): un punto de decision para todo lo roto al arrancar

Absorbe la guarda de publicadores y reporta ademas FMP, AV, FRED, Anthropic, R2,
Telegram y los switches con valor invalido — una sola alerta con una linea por
componente, en el primer sitio donde existen a la vez el canal y el \`event_id\`.

\`run_weekly_close\` devuelve el codigo de salida: 0 si corrio, 1 si abortó por
configuracion. Las salidas que ya existian devuelven todas 0 porque su desenlace
queda en la fila; el 1 es para el abort que no deja fila y necesita un rastro
fuera del proceso — el caso de Telegram roto, que no puede avisar de si mismo.

El orden de las dos primeras ramas es obligatorio: un switch invalido deja el
cliente sin construir, identico a un apagado deliberado, y con las ramas al
reves \`USE_TELEGRAM=maybe\` saldria con 0 y en silencio.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Las guardas de `_fetch_weekly_close`

Con el constructor total, `self.fmp` y `self.av` pueden ser `None` cuando el ETL corre. La de AV es la que importa: con AV apagado y FMP fallando en caliente, el código llegaba a `self.av.get_daily_prices` con `None`, y la alerta del `except` general habría dicho `'NoneType' object has no attribute 'get_daily_prices'` — la alerta culpando a lo que no es, que es el patrón de la divergencia 1 de ADR-009.

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:225-250`
- Test: `tests/unit/test_orchestrator_startup.py` (añadir)

- [ ] **Step 1: Escribir los tests que fallan**

Añadir a `tests/unit/test_orchestrator_startup.py`:

```python
def test_a_missing_av_names_itself_instead_of_an_attribute_error(data_orch):
    """La divergencia 1 otra vez: la alerta tiene que nombrar la causa real."""
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av = None
    data_orch.component_errors["av"] = "Se requiere ALPHA_VANTAGE_API_KEY."

    with pytest.raises(RuntimeError) as exc:
        data_orch._fetch_weekly_close()

    assert "ALPHA_VANTAGE_API_KEY" in str(exc.value)
    assert "NoneType" not in str(exc.value)


def test_a_switched_off_av_says_so(data_orch):
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av = None

    with pytest.raises(RuntimeError) as exc:
        data_orch._fetch_weekly_close()

    assert "USE_AV" in str(exc.value)


def test_the_etl_refuses_to_run_without_fmp(data_orch):
    """No lo alcanza ningun camino: el punto de decision aborta antes.

    Existe para que, si alguien reordena las ramas, esto muera con un motivo
    legible y no con un `AttributeError`.
    """
    data_orch.fmp = None

    with pytest.raises(RuntimeError, match="punto de decisión"):
        data_orch._fetch_weekly_close()
```

Y el fixture, al principio del fichero (después de los imports):

```python
@pytest.fixture
def data_orch():
    """Orquestador con lo justo para ejercitar `_fetch_weekly_close`."""
    from unittest.mock import MagicMock

    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch._allow_mock = False
    orch.component_errors = {}
    orch.switch_errors = {}
    orch.macro_error = None
    orch.fred = None
    orch.fmp = MagicMock()
    orch.av = MagicMock()
    return orch
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_startup.py -k "av or fmp" -q`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'get_daily_prices'`, que es exactamente el mensaje que estos tests existen para eliminar.

- [ ] **Step 3: Escribir las guardas**

Al principio de `_fetch_weekly_close`, justo después de `logger.info("orchestrator_fetching_data")`:

```python
        # No lo alcanza ningun camino: el punto de decision aborta cuando FMP no
        # esta. Es la red por si alguien reordena sus ramas — mejor un motivo
        # legible que un `AttributeError` con el humano ya esperando aprobar.
        if self.fmp is None:
            raise RuntimeError(
                "FMP no está construido: el punto de decisión debía haber "
                "abortado antes de llegar acá."
            )
```

Y dentro del `except` de FMP, reemplazar el `try` interno por:

```python
            try:
                # AV degrada, asi que el pipeline llega hasta aca con `self.av`
                # en None. Sin esta guarda salia un `AttributeError` y la alerta
                # del `except` general culpaba a un bug en vez de nombrar a AV.
                if self.av is None:
                    motivo = self.component_errors.get(
                        "av", f"apagado con {USE_AV_VAR}=false"
                    )
                    raise RuntimeError(f"Alpha Vantage no disponible: {motivo}")
                sp500_df = self.av.get_daily_prices("SPY", outputsize="compact")
                nasdaq_df = self.av.get_daily_prices("QQQ", outputsize="compact")
            except Exception as av_error:
                logger.warning("av_failed_falling_back_to_mock", error=str(av_error))
                data_source = "mock"

                if not self._allow_mock:
                    raise RuntimeError(
                        "Todas las fuentes de datos fallaron (FMP, AV). "
                        f"Última causa: {av_error}. "
                        "Mock Data bloqueado en producción. "
                        "Set ALLOW_MOCK_DATA=true solo en desarrollo."
                    ) from av_error
```

El `Última causa` es lo que hace que la alerta del `except` general nombre a AV: `str(e)` no recorre el `from`.

- [ ] **Step 4: Correr, gates y commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS.

```bash
./.venv/Scripts/python.exe -m ruff format src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m mypy src/ scripts/
git add -A
git commit -m "fix(etl): la rama de fallback mira si Alpha Vantage existe

Con AV apagado o sin key el pipeline sigue —degrada—, asi que un fallo de FMP en
caliente llegaba a \`self.av.get_daily_prices\` con None: salia un
\`AttributeError\` y la alerta culpaba a un bug en vez de nombrar a AV. Es la
divergencia 1 de ADR-009 con otro disfraz.

El motivo viaja tambien en el RuntimeError final, porque \`str(e)\` no recorre la
cadena del \`from\` y la alerta solo ve el mensaje de arriba.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: El `except` general alerta

Hoy loggea, marca `failed` y re-levanta sin avisar: AV caída en caliente, una cifra fuera de rango o un render fallido dejan una fila que nadie mira.

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py` (el `except Exception` de `run_weekly_close`)
- Test: `tests/integration/test_orchestrator_exit_states.py` (añadir)

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/integration/test_orchestrator_exit_states.py`:

```python
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
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py::test_a_critical_failure_alerts_before_marking_the_row -q`
Expected: FAIL — `Expected 'send_alert' to have been called once. Called 0 times.`

- [ ] **Step 3: Escribir la alerta**

En el `except Exception as e:` de `run_weekly_close`, entre el `logger.error` y el `mark_failed`:

```python
                # El local `telegram` puede no estar ligado: la excepción pudo
                # ocurrir antes de que se asignara, así que acá se mira el
                # atributo. `send_alert` nunca levanta —devuelve un bool—, lo
                # que importa dentro de un manejador de fallos.
                if self.telegram is not None:
                    self.telegram.send_alert(
                        "⛔ El cierre semanal murió a mitad de camino.\n\n"
                        f"Motivo: {e}\n\n"
                        "El evento queda en `failed`, así que la próxima run lo "
                        "reintenta."
                    )
```

- [ ] **Step 4: Mutación**

Quitar esa llamada y correr la suite: tiene que caer `test_a_critical_failure_alerts_before_marking_the_row` y **sólo** ése. Revertir.

- [ ] **Step 5: Correr, gates y commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS.

```bash
./.venv/Scripts/python.exe -m ruff format src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m mypy src/ scripts/
git add -A
git commit -m "feat(observability): una run que muere avisa antes de marcar failed

Cierra la mitad en caliente de la misma invisibilidad: hasta hoy el unico abort
que alertaba era el pre-lock de publicadores, y AV caida, una cifra fuera de
rango o un render fallido dejaban una fila \`failed\` que nadie mira.

Mira \`self.telegram\` y no el local: la excepcion pudo ocurrir antes de que el
local se ligara.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: `.env.example`

**Files:**
- Modify: `.env.example`
- Test: `tests/unit/test_check_publishers.py` (añadir)

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/unit/test_check_publishers.py`:

```python
def test_the_example_declares_the_six_component_switches_commented(check_publishers):
    """Comentados y no puestos, a diferencia de las dos banderas de publicación.

    Ausente significa encendido, así que no hay nada que copiar al `.env`:
    declararlos sin comentar obligaría a tenerlos y la deriva avisaría de una
    ausencia que es correcta. Entran en `documentadas` vía `_commented_names`,
    igual que `STATE_DB_PATH`.
    """
    ejemplo = ROOT / ".env.example"
    comentadas = check_publishers._commented_names(ejemplo)
    declaradas = check_publishers._parse_env_file(ejemplo)

    for var in (
        "USE_FMP",
        "USE_AV",
        "USE_FRED",
        "USE_ANTHROPIC",
        "USE_R2",
        "USE_TELEGRAM",
    ):
        assert var in comentadas, var
        assert var not in declaradas, var
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_check_publishers.py -k switches -q`
Expected: FAIL — `AssertionError: USE_FMP`.

- [ ] **Step 3: Escribir el bloque en `.env.example`**

Justo antes de la sección `# --- Publicacion por red ---`:

```
# --- Switches por componente ---
# Un switch por API, con la misma semantica estricta que PUBLISH_X: ausente o
# vacio -> encendido; true/false; cualquier otro valor aborta la run con aviso,
# porque no se puede leer la intencion y adivinar es silencioso.
#
# Apagado no alerta: es una decision, no un fallo. Encendido y sin credenciales
# si alerta, y eso es lo que cierra el agujero que ADR-009 tenia anotado — hasta
# hoy una key rotada se leia igual que una decision.
#
# Van comentados a proposito: ausente significa encendido, asi que no hay nada
# que copiar al .env, y un .env sin copiar deja todo encendido y por lo tanto
# todo alertando, en vez de callarse.
#
# OJO con USE_FMP y USE_TELEGRAM: apagarlos no degrada, aborta. FMP es la unica
# fuente con ruta capaz de publicar (la de AV la rechaza el validador por rango,
# ADR-009 divergencia 4), y sin Telegram no hay aprobacion humana (ADR-004). Los
# dos abortan en silencio: apagarlos es una decision.
# USE_FMP=true
# USE_AV=true
# USE_FRED=true
# USE_ANTHROPIC=true
# USE_R2=true
# USE_TELEGRAM=true
```

Y en el comentario de la sección `# --- Publicacion por red ---`, añadir una línea: `# Mismo mecanismo que los USE_* de arriba; conservan el prefijo PUBLISH_ porque` / `# preceden al switch generico y "publicar" describe mejor lo que deciden.`

- [ ] **Step 4: Correr, gates y commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q`
Expected: PASS.

```bash
git add -A
git commit -m "docs(env): declarar los seis switches nuevos, comentados

Comentados y no puestos: ausente significa encendido, asi que no hay nada que
copiar y la deriva no tiene que avisar de una ausencia correcta. Entran en
\`documentadas\` via \`_commented_names\`, igual que STATE_DB_PATH.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: ADR-009

**Files:**
- Modify: `docs/adr/009-degradation-policy.md`

- [ ] **Step 1: Reescribir el tercer eje**

Buscar la formulación del tercer eje (`grep -n "declarado opcional" docs/adr/009-degradation-policy.md`) y reemplazarla por:

> Un componente **apagado por su switch** no participa, y no participar no es degradar. Un componente **encendido** al que le faltan credenciales es un fallo, y alerta.
>
> El switch es lo que el código lee. Las declaraciones de ADR-001 —el LLM fuera del path numérico— y de ADR-007 —R2 opcional— no desaparecen: siguen siendo el motivo por el que un componente *puede* apagarse, pero dejaron de ser la señal. La diferencia importa porque una declaración en un ADR no distingue una decisión de una key rotada, y un `false` tipeado por una persona sí.

- [ ] **Step 2: Cerrar el coste anotado en §Consecuencias**

Reemplazar la última frase del bullet del tercer eje (`Cerrarlo pide declarar qué opcionales *deberían* estar activos y avisar cuando uno deja de estarlo; hoy eso no existe.`) por:

> **Cerrado el 2026-08-29 con un switch por componente.** La no-configuración dejó de ser la señal: el silencio ahora exige un `false` explícito, y una key rotada o un `.env` sin copiar dejan el componente encendido y por lo tanto alertando. Lo que queda como coste es más chico y de otra clase: quien apaga un componente tiene que acordarse de volver a encenderlo, y nada se lo recuerda.

- [ ] **Step 3: Cerrar la limitación (d)**

Reemplazar el cuerpo de **(d)** por:

> **Cerrada el 2026-08-29.** Ningún componente con credenciales puede matar
> `MacroOrchestrator.__init__`: los ocho pasan por `build_component`, que anota
> el motivo en vez de dejar salir el `ValueError`. Todo lo que quedó roto o
> apagado al arrancar se reporta desde un punto de decisión único al principio
> de `run_weekly_close`, que es el primer sitio donde existen a la vez el canal
> de aviso y el `event_id`. El orden de construcción dejó de ser lógica, que era
> la raíz del problema y no su síntoma: FMP y Alpha Vantage se construían antes
> que Telegram, así que cuando reventaban no había con qué avisar.

Y añadir a continuación el límite que queda:

> **Lo que no se puede cerrar:** Telegram encendido y sin credencial. No hay canal para avisar de que no hay canal, y un segundo canal no existe. Esa run deja un log nombrado con el cuadro completo de motivos y sale con código `1`; el código de salida es lo único que cruza el borde del proceso. Hoy nadie lo mira —nada corre `main.py` en un schedule—, así que este límite se cobra el día que el pipeline corra desatendido, junto con el punto de la idempotencia bajo un entorno efímero.

- [ ] **Step 4: Actualizar la tabla de catorce filas**

Las tres filas de «Sin key» de FRED, Anthropic y R2 cambian de `**No participa** — opcional sin configurar …, sin alerta` a `**Degrada**, con alerta desde el punto de decisión` y se les añade una fila hermana `Apagado con USE_…=false` → `**No participa**, sin alerta`. Filas nuevas:

| Componente | Fallo | Política | Estado que deja |
|---|---|---|---|
| FMP (índices) | Sin key, o `USE_FMP=false` | **Aborta** antes del lock — sin key alerta; apagado, en silencio | Ninguna fila |
| Alpha Vantage (índices) | Sin key | **Degrada** — el fallback queda ausente, con alerta que dice que esa ruta tampoco publicaría | — |
| Telegram | Sin credenciales | **Aborta** — sin canal ni HITL; log nombrado y salida `1`, **sin alerta posible** | Ninguna fila |
| Telegram | `USE_TELEGRAM=false` | **Aborta** en silencio — pausa deliberada del pipeline entero | Ninguna fila |
| Cualquier switch | Valor que no es `true` ni `false` | **Aborta** con alerta — no se pudo leer la intención | Ninguna fila |
| Cualquier excepción | Dentro de `run_weekly_close` | **Aborta** con alerta que nombra el motivo | `failed` |

- [ ] **Step 5: Anotar la divergencia 4**

Añadir al final de la divergencia 4:

> Mientras esto siga así, **FMP sin key aborta** en el punto de decisión: no tiene ruta viva. El día que se publique sólo el retorno desde AV, FMP sin key pasa a ser una degradación y hay que mover su rama del bloque de abortos al de degradaciones. Queda escrito de antemano para no volver a deducirlo.

- [ ] **Step 6: Recorrer la tabla entera contra el código**

Es el ejercicio que destapó las tres divergencias la primera vez y la fila mentirosa de R2 la segunda. Para cada fila, abrir el sitio del código que la implementa y comprobar que dice lo mismo. Anotar cualquier discrepancia como divergencia nueva en vez de arreglarla en silencio.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs: ADR-009 cierra la limitacion (d) y reescribe el tercer eje

El eje deja de descansar en una declaracion de otro ADR y pasa a descansar en un
switch explicito, que es lo que la propia seccion de Consecuencias pedia. Con
eso el coste anotado el 2026-08-29 se cierra: una key rotada ya no se lee como
una decision.

Queda un limite que no se puede cerrar sin un segundo canal: Telegram encendido
y sin credencial no puede avisar de si mismo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Verificación final

- [ ] **Step 1: La suite entera con cobertura, como en CI**

Run:
```bash
./.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -v --cov=src/macro_pipeline --cov-report=term-missing --cov-fail-under=60
```
Expected: PASS, cobertura ≥ 83% (venía de 87.43%).

- [ ] **Step 2: Los tres gates**

Run: `./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/ && ./.venv/Scripts/python.exe -m mypy src/ scripts/`
Expected: `All checks passed!`, `N files already formatted`, `Success: no issues found`.

- [ ] **Step 3: El script de credenciales sigue corriendo**

Run: `./.venv/Scripts/python.exe scripts/check_publishers.py`
Expected: corre y reporta; el import de `macro_pipeline.components` no rompe nada.

- [ ] **Step 4: Push y CI sobre el HEAD exacto**

```bash
git push origin main
gh run list --limit 1 --json databaseId,headSha,status
gh run watch <id> --exit-status
```
Expected: `success`, y el `headSha` tiene que ser el HEAD local. Verde en local no es verde en Actions hasta ver el run.

- [ ] **Step 5: Actualizar la memoria**

En `macropipeline-pending-work.md`, cerrar el punto 13 con la fecha, los commits, el número de run y lo que este trabajo dejó aprendido. El backlog baja a tres: el estado bajo Routines efímero, el retorno desde AV, y el token de LinkedIn.
