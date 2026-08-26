# La capa LLM sin configurar no participa — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `MacroOrchestrator` sobreviva a una `ANTHROPIC_API_KEY` ausente publicando el cierre semanal con titular genérico y sin alertar, en vez de morir en el constructor sin alerta y sin fila de estado.

**Architecture:** `LLMClient` y `ValidatorAgent` pasan a construirse dentro de un `try/except ValueError` en `__init__`, igual que `FREDClient` y `R2Client`. La fase LLM abre con una guarda `self.llm is None` que produce el titular genérico —extraído a `_generic_headline()` para que no haya dos copias del texto— sin mandar nada a Telegram, y persiste `prompt_version` y `validator_approved` en NULL porque no ocurrió ninguna llamada que registrar.

**Tech Stack:** Python 3.12, pytest, mypy, structlog, SQLite. Spec en `docs/superpowers/specs/2026-08-26-llm-layer-optional-design.md`.

---

## Entorno — leer antes de empezar

**Usar siempre `./.venv/Scripts/python.exe -m ...`**, nunca `python` a secas. El Python global de esta máquina no tiene el paquete en editable y le faltan `anthropic`, `playwright` y `boto3`: con él `mypy src` inventa dos errores `no-any-return` y los tests del orquestador ni colectan.

**Trampa de entorno que ya mordió una vez:** `tests/contract/conftest.py` hace `load_dotenv()` al importarse, y pytest lo importa al recorrer `tests/` aunque los contract tests estén deseleccionados. O sea que **el `.env` del desarrollador se cuela en los unit tests**. Por eso el Task 2 usa `monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)` de forma explícita: sin eso el test pasaría en CI (donde no hay `.env`) y fallaría en la máquina de Simon, o al revés.

## Estructura de ficheros

| Fichero | Qué pasa | Responsabilidad |
|---|---|---|
| `src/macro_pipeline/orchestration/main.py` | Modificar | `_generic_headline()` nueva a nivel de módulo; guarda en `__init__`; guarda en la fase LLM; `prompt_version` como variable local |
| `tests/unit/test_orchestrator_llm.py` | Crear | Que `__init__` sobreviva sin key, y que siga construyendo la capa con key |
| `tests/integration/test_orchestrator_exit_states.py` | Modificar | La run sin capa LLM: publica, no alerta, deja la fila con NULL. Es el fichero con `StateDB` **real** |
| `docs/adr/009-degradation-policy.md` | Modificar | Fila nueva en la tabla, el tercer eje cita a ADR-001, la limitación (d) deja de estar "sin decidir" |

---

## Task 1: Extraer `_generic_headline()`

Refactor puro: el texto no cambia, solo deja de estar enterrado dentro de `if degradation:`. Se hace primero y por separado para que el diff del cambio de comportamiento no venga mezclado con un movimiento de código.

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/unit/test_orchestrator_llm.py` (crear)

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/unit/test_orchestrator_llm.py` con este contenido completo:

```python
"""La capa LLM declarada auxiliar: sin key no participa, y no alerta.

ADR-009, tercer eje: un componente declarado opcional que no esta configurado
no participa, y no participar no es degradar. La declaracion es ADR-001, que
define la capa LLM como auxiliar — el LLM no toca numeros, solo redacta.

La asimetria con la API caida es a proposito y esta cubierta por
`test_a_dead_generator_alerts_even_if_the_validator_approves` en
`tests/integration/test_orchestrator_persistence.py`: roto alerta, sin
configurar no.
"""

from datetime import date

from macro_pipeline.orchestration.main import _generic_headline
from macro_pipeline.validators.schemas import WeeklyCloseData


def _data() -> WeeklyCloseData:
    return WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=-0.019,
        macro=None,
    )


def test_the_generic_headline_carries_both_real_returns():
    """La premisa con la que ADR-009 acepta degradar aca.

    "El bloque generico lleva las cifras reales — las pone el pipeline, no el
    modelo". Si el texto pierde una cifra, esa premisa deja de ser cierta y la
    degradacion pasa a costar informacion en vez de solo redaccion.
    """
    titular = _generic_headline(_data())

    assert "+1.20%" in titular
    assert "-1.90%" in titular
    assert "S&P500" in titular
    assert "NASDAQ" in titular
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_llm.py -v`

Expected: FAIL — `ImportError: cannot import name '_generic_headline' from 'macro_pipeline.orchestration.main'`

- [ ] **Step 3: Añadir la función a nivel de módulo**

En `src/macro_pipeline/orchestration/main.py`, justo después de la definición de `_PROMPT_VERSION` y antes de `class MacroOrchestrator:`, añadir:

```python
def _generic_headline(data: WeeklyCloseData) -> str:
    """El titular que publica el pipeline cuando la capa LLM no redacta.

    Vive acá y no dentro de la rama de degradación porque lo usan dos caminos
    que alertan distinto —la capa LLM caída, que avisa, y la capa LLM sin
    configurar, que no—, y la premisa con la que ADR-009 acepta degradar ahí es
    que «el bloque genérico lleva las cifras reales». Con dos copias esa
    premisa se puede volver falsa en una sola de ellas, que es donde nadie
    mira.
    """
    return (
        f"📊 Cierre de Mercado Semanal:\n"
        f"S&P500: {data.sp500_weekly_return * 100:+.2f}%\n"
        f"NASDAQ: {data.nasdaq_weekly_return * 100:+.2f}%"
    )
```

`WeeklyCloseData` ya está importado en ese fichero desde `macro_pipeline.validators.schemas`; no hace falta tocar los imports.

- [ ] **Step 4: Reemplazar el literal en la rama de degradación**

En la fase LLM de `run_weekly_close`, dentro del bloque `if degradation:`, reemplazar:

```python
                        headline = (
                            f"📊 Cierre de Mercado Semanal:\n"
                            f"S&P500: {data.sp500_weekly_return * 100:+.2f}%\n"
                            f"NASDAQ: {data.nasdaq_weekly_return * 100:+.2f}%"
                        )
```

por:

```python
                        headline = _generic_headline(data)
```

- [ ] **Step 5: Correr el test nuevo y los dos que fijan el texto por el camino de degradación**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_llm.py tests/integration/test_orchestrator_persistence.py -v
```

Expected: PASS todos. En particular `test_a_dead_generator_publishes_the_generic_block_with_the_real_figures` tiene que seguir pasando: es el que prueba que la extracción no cambió el texto.

- [ ] **Step 6: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/unit/test_orchestrator_llm.py
git commit -m "refactor(orchestration): extraer el titular generico de la rama de degradacion

A partir de ahora lo usan dos caminos que alertan distinto, y la premisa con
la que ADR-009 acepta degradar ahi es que el bloque generico lleva las cifras
reales. Con dos copias esa premisa se puede volver falsa en una sola.

Texto identico: test_a_dead_generator_publishes_the_generic_block_with_the
_real_figures lo fija por el camino del generador caido."
```

---

## Task 2: La guarda en `__init__`

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/unit/test_orchestrator_llm.py`

- [ ] **Step 1: Escribir los dos tests que fallan**

Los dos, y **por dirección**: con key construye, sin key no. Dos asserts ciegos a la dirección fueron exactamente lo que dejó a la alerta de publicadores mintiendo en las dos mitades con 148 tests en verde.

Añadir al final de `tests/unit/test_orchestrator_llm.py`:

```python
import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator

# Las cuatro credenciales de los componentes que `__init__` construye *antes*
# de llegar a la capa LLM y que no tienen guarda: sin ellas revienta por otro
# motivo y el test no probaria nada. Son el resto de la limitacion (d) de
# ADR-009, que sigue abierta.
_REQUIRED = {
    "FMP_API_KEY": "fmp-test",
    "ALPHA_VANTAGE_API_KEY": "av-test",
    "TELEGRAM_BOT_TOKEN": "tg-test",
    "TELEGRAM_CHAT_ID": "123456",
}


@pytest.fixture
def buildable_env(monkeypatch, tmp_path):
    """Entorno minimo para construir un orquestador real.

    `STATE_DB_PATH` va a un temporal a proposito: sin eso `StateDB` crearia
    `~/.macropipeline/state.db` de verdad al correr los tests.

    Las delenv son obligatorias y no defensivas: `tests/contract/conftest.py`
    hace `load_dotenv()` al importarse, y pytest lo importa al recorrer
    `tests/` aunque los contract tests esten deseleccionados. Sin ellas el
    resultado depende de si la maquina tiene `.env`.
    """
    for name, value in _REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    for name in ("FRED_API_KEY", "PUBLISH_X", "PUBLISH_LINKEDIN"):
        monkeypatch.delenv(name, raising=False)


def test_a_missing_anthropic_key_does_not_kill_the_run(buildable_env, monkeypatch):
    """Sin key el constructor levantaba y la run moria antes de empezar.

    No habia alerta, no habia fila de estado, y la semana siguiente pasaba lo
    mismo en silencio. La tabla de ADR-009 declara que la capa LLM degrada, asi
    que tratarla como fatal en el constructor era la politica al reves.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    orch = MacroOrchestrator()

    assert orch.llm is None
    assert orch.validator_agent is None


def test_the_llm_layer_is_still_built_when_the_key_is_there(
    buildable_env, monkeypatch
):
    """La otra direccion, y no es ceremonia.

    Sin este test, apagar la capa LLM siempre —o no construirla nunca— dejaria
    el de arriba en verde, y el pipeline publicaria el bloque generico todas
    las semanas con la key puesta.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    orch = MacroOrchestrator()

    assert orch.llm is not None
    assert orch.validator_agent is not None
```

- [ ] **Step 2: Correr los tests y verificar que uno falla y el otro pasa**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_llm.py -v`

Expected: `test_a_missing_anthropic_key_does_not_kill_the_run` FALLA con `ValueError: Se requiere ANTHROPIC_API_KEY en el entorno.`; `test_the_llm_layer_is_still_built_when_the_key_is_there` PASA ya. Que el segundo pase antes del cambio es correcto: es un control, no una funcionalidad nueva.

- [ ] **Step 3: Envolver la construcción**

En `MacroOrchestrator.__init__`, reemplazar:

```python
        self.llm = LLMClient()
        self.validator_agent = ValidatorAgent(self.llm)
        self.renderer = PlaywrightEngine()
```

por:

```python
        # ADR-001 declara auxiliar a la capa LLM —el LLM no toca números, solo
        # redacta— y ADR-009 se apoya en esa declaración para que degrade. Un
        # componente que la política declara prescindible no puede ser fatal en
        # el constructor: sin key la run moría antes de `run_weekly_close`, sin
        # alerta y sin fila de estado.
        #
        # Los dos en el mismo `try`: `ValidatorAgent` recibe el cliente, así
        # que sin generador no hay validador que construir.
        #
        # `except ValueError` y no uno ancho como el de R2: acá no hay red de
        # por medio. `LLMClient.__init__` solo levanta cuando falta la key, y
        # construir el cliente de `anthropic` no hace ninguna llamada.
        self.llm: LLMClient | None
        self.validator_agent: ValidatorAgent | None
        try:
            self.llm = LLMClient()
            self.validator_agent = ValidatorAgent(self.llm)
        except ValueError as e:
            logger.warning("llm_not_configured", reason=str(e))
            self.llm = None
            self.validator_agent = None

        self.renderer = PlaywrightEngine()
```

- [ ] **Step 4: Correr los tests y verificar que pasan los dos**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_llm.py -v`

Expected: 3 passed (el de Task 1 más estos dos).

- [ ] **Step 5: Correr mypy**

Run: `./.venv/Scripts/python.exe -m mypy src`

Expected: `Success: no issues found`. Si aparecen errores `no-any-return`, es que se corrió con el Python global — repetir con `./.venv/Scripts/python.exe`.

- [ ] **Step 6: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/unit/test_orchestrator_llm.py
git commit -m "feat(orchestration): la capa LLM sin key deja de matar la run

ADR-009 declara que la capa LLM degrada, pero el constructor la trataba como
fatal: sin ANTHROPIC_API_KEY la run moria antes de run_weekly_close, sin
alerta y sin fila de estado. Ahora se envuelve como ya se envuelven FREDClient
y R2Client.

Los dos tests van por direccion: sin key no construye, con key si."
```

---

## Task 3: La fase LLM no participa, y no alerta

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/integration/test_orchestrator_exit_states.py`

- [ ] **Step 1: Escribir los dos tests que fallan**

Van en `tests/integration/test_orchestrator_exit_states.py` porque ese fichero tiene un `StateDB` **real** sobre un fichero temporal — `test_orchestrator_persistence.py` lo mockea y no puede verificar qué quedó escrito.

Añadir al final del fichero:

```python
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
```

**Ojo con el primer assert:** `send_approval_request` se llama con `text=` como keyword (`text=headline, image_bytes=image_bytes`), por eso el test lee `call_args[1]["text"]` y no `call_args[0][0]`. Verificarlo en el código antes de dar por bueno un fallo del test.

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k llm_layer -v
```

Expected: los dos FALLAN con `AttributeError: 'NoneType' object has no attribute 'generate_headline'`.

- [ ] **Step 3: Poner la guarda al principio de la fase LLM**

En `run_weekly_close`, la fase LLM empieza con el comentario `# ── FASE LLM ───` seguido del bloque `with (self.tracer.start_as_current_span("llm_headline") ...)`. Reemplazar la apertura de esa fase por:

```python
                # ── FASE LLM ───────────────────────────────────────────────────
                if self.llm is None or self.validator_agent is None:
                    # ADR-009, tercer eje: ADR-001 declara auxiliar a la capa
                    # LLM, así que sin key no participa — y no participar no es
                    # degradar. Sin alerta, por el mismo motivo que FRED sin
                    # key. La asimetría con la API caída (que sí alerta) es el
                    # punto: si llega una alerta, es porque algo se rompió.
                    #
                    # No se abre el span `llm_headline`: no hubo llamada.
                    logger.info("llm_layer_not_participating")
                    headline = _generic_headline(data)
                    validator_approved = None
                    prompt_version = None
                else:
                    with (
                        self.tracer.start_as_current_span("llm_headline")
                        if self.tracer
                        else nullcontext()
                    ):
                        # (el cuerpo actual, ver abajo)
```

En lugar del comentario va **el cuerpo actual entero de ese `with`, sin ningún
otro cambio, movido 4 espacios a la derecha**. Los límites exactos, para que no
haya ambigüedad sobre qué se mueve:

- **Primera línea:** `data_str = (`
- **Última línea:** `headline = _generic_headline(data)` — la del bloque
  `if degradation:`, que el Task 1 dejó así.

No se toca ni una línea de ese cuerpo: los tres textos de degradación, el
`logger.error("llm_layer_degraded", ...)` y el `send_alert` quedan idénticos. Si
el diff de este paso muestra algo que no sea un cambio de indentación dentro de
esos límites, está mal.

Después de ese cuerpo, todavía dentro del `else` pero **fuera** del `with`,
añadir:

```python
                    prompt_version = _PROMPT_VERSION
```

Esa línea va al nivel del `with` (no dentro de él): se ejecuta pase lo que pase en la rama normal.

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v
```

Expected: PASS todos, incluidos los que ya estaban.

- [ ] **Step 5: Correr el control negativo, que ya existía**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_persistence.py -v
```

Expected: PASS todos. Los dos que importan y **no hay que escribir porque ya están**:
- `test_a_dead_generator_alerts_even_if_the_validator_approves` — con la key puesta y la API caída **sí** alerta. Es lo que impide que apagar el silencio de más apague también la alerta real.
- `test_the_alert_does_not_blame_the_validator_when_it_never_reviewed` — los tres textos de degradación siguen intactos.

Si alguno de estos dos se pone rojo, el cambio se pasó de alcance: revertir y revisar, no ajustar el test.

- [ ] **Step 6: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(orchestration): la capa LLM sin configurar no participa y no alerta

Tercer eje de ADR-009: un componente declarado opcional que no esta
configurado no participa, y no participar no es degradar. La declaracion es
ADR-001, que define la capa LLM como auxiliar.

La asimetria con la API caida es el punto y esta cubierta por los dos tests
de generador caido que ya existian: roto alerta, sin configurar no."
```

---

## Task 4: La fila registra NULL, no una llamada que no ocurrió

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/integration/test_orchestrator_exit_states.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir al final de `tests/integration/test_orchestrator_exit_states.py`:

```python
def test_a_run_without_the_llm_layer_records_no_prompt_and_no_verdict(data, state):
    """NULL significa "no ocurrio", igual que las seis columnas macro sin FRED.

    Escribir la version de prompt afirmaria una llamada que no se hizo, y
    `prompt_version` existe justamente para poder reproducir un titular
    historico: uno que escribio el pipeline no tiene prompt que lo reproduzca.
    `validator_approved=False` se leeria como "el validador lo rechazo", que
    tampoco paso.
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
```

- [ ] **Step 2: Correr los tests y verificar que uno falla**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k "prompt" -v
```

Expected: `test_a_run_without_the_llm_layer_records_no_prompt_and_no_verdict` FALLA — `assert 'headline=v1.4/validator=.../model=claude-haiku-4-5' is None`. `test_a_normal_run_still_records_the_prompt_version` PASA ya.

- [ ] **Step 3: Pasar `prompt_version` por la variable local**

En la llamada a `self.state.mark_as_published(...)`, reemplazar:

```python
                        prompt_version=_PROMPT_VERSION,
```

por:

```python
                        prompt_version=prompt_version,
```

La variable ya la setean las dos ramas del Task 3 (`None` en la de la capa apagada, `_PROMPT_VERSION` en la normal). `validator_approved` no necesita ningún cambio acá: ya se pasa por variable, y `mark_as_published` **ya tiene escrita** la rama `int(validator_approved) if validator_approved is not None else None` — era código inalcanzable porque `bool(review.get("approved"))` nunca daba `None`.

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/integration/ -v
```

Expected: PASS todos.

- [ ] **Step 5: Correr mypy**

Run: `./.venv/Scripts/python.exe -m mypy src`

Expected: `Success: no issues found`. Si se queja del tipo de `validator_approved`, es que alguna rama no lo asigna: las dos tienen que hacerlo.

- [ ] **Step 6: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(state): una run sin capa LLM no registra prompt ni veredicto

Escribir _PROMPT_VERSION afirmaria una llamada que no ocurrio, y
validator_approved=False se leeria como un rechazo que no hubo. NULL es lo que
ya significan las seis columnas macro cuando FRED no participa.

Pone a correr la rama None de mark_as_published, que era inalcanzable porque
bool(review.get(...)) nunca devolvia None."
```

---

## Task 5: Verificación por mutación

Antes de tocar el ADR, no después. Es lo que distingue "los tests pasan" de "los tests verifican algo": la alerta de publicadores podía mentir en las dos mitades con 148 tests en verde, y el reetiquetado publicó el texto genérico 9 de cada 10 semanas con cuatro gates verdes.

- [ ] **Step 1: Mutación 1 — quitar la guarda de `__init__`**

Cambiar temporalmente en `__init__` el bloque `try/except` por `self.llm = LLMClient()` y `self.validator_agent = ValidatorAgent(self.llm)` a secas.

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_llm.py -v`

Expected: FALLA `test_a_missing_anthropic_key_does_not_kill_the_run` con `ValueError`. Revertir la mutación.

- [ ] **Step 2: Mutación 2 — alertar en la rama nueva**

Añadir temporalmente `self.telegram.send_alert("prueba")` dentro de la rama `if self.llm is None or self.validator_agent is None:`.

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v`

Expected: FALLA **exactamente** `test_a_run_without_the_llm_layer_says_nothing`. Si falla algún otro, o si no falla ninguno, el conjunto de tests no está fijando lo que dice fijar. Revertir la mutación.

- [ ] **Step 3: Mutación 3 — registrar la versión de prompt igual**

Cambiar temporalmente `prompt_version = None` por `prompt_version = _PROMPT_VERSION` en la rama de la capa apagada.

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k prompt -v`

Expected: FALLA `test_a_run_without_the_llm_layer_records_no_prompt_and_no_verdict`. Revertir.

- [ ] **Step 4: Mutación 4 — devolver `FALLBACK_HEADLINE` en vez del bloque genérico**

Cambiar temporalmente `headline = _generic_headline(data)` de la rama nueva por `headline = FALLBACK_HEADLINE`.

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k llm_layer -v`

Expected: FALLA `test_a_run_without_the_llm_layer_publishes_the_generic_block` — es el que prueba que la degradación cuesta redacción y no información. Revertir.

- [ ] **Step 5: Confirmar que el árbol quedó limpio tras las cuatro reversiones**

Run: `git status --short`

Expected: sin cambios. Si aparece algo, es una mutación que quedó sin revertir.

---

## Task 6: ADR-009

El ADR es el sitio donde vive la política; dejarlo diciendo "sin decidir" cuando ya está decidido es exactamente el drift que este mismo ADR documenta haber encontrado en dos de sus propias filas.

**Files:**
- Modify: `docs/adr/009-degradation-policy.md`

- [ ] **Step 1: Añadir la fila a la tabla por componente**

En la tabla «La política, por componente», justo **antes** de la fila `| Anthropic (generador) | API caída | ...`, insertar:

```markdown
| Anthropic (capa LLM) | Sin key | **No participa** — opcional sin configurar (ADR-001 la declara auxiliar), sin alerta | — |
```

- [ ] **Step 2: Extender el tercer eje**

En la sección «El tercer eje: lo declarado opcional no degrada», reemplazar el párrafo que empieza con «De acá sale que **FRED sin key y R2 sin configurar son la misma cosa**» por:

```markdown
De acá sale que **FRED sin key, R2 sin configurar y la capa LLM sin key son la
misma cosa**: no participan, y no gastan una alerta. Los tres fallando *estando
configurados* sí alertan.

Para la capa LLM la declaración es **ADR-001**, que la define como auxiliar: el
LLM no toca números y solo redacta un titular a partir de cifras ya calculadas
y validadas. Esta misma política ya se apoyaba en esa definición para que la
API caída degrade; que la key ausente no participe es la otra mitad. Sin capa
LLM el cierre semanal se publica igual y sigue siendo correcto, porque las
cifras las pone el pipeline — lo que se pierde es redacción, no información.
```

- [ ] **Step 3: Cerrar la mitad de Anthropic de la limitación (d)**

En la limitación **(d)**, reemplazar la frase final que empieza con «**Anthropic no entra en esta lista, y su caso es otro:**» y termina en «**Queda sin decidir.**» por:

```markdown
**Anthropic no entra en esta lista, y su caso era otro.** — *Decidido y cerrado
el 2026-08-26.* Sin `ANTHROPIC_API_KEY` el constructor también levantaba, pero
lo que divergía ahí no era un aviso que faltara sino que el constructor tratara
como fatal a un componente que esta política declara degradable. Ahora
`__init__` lo envuelve como ya envolvía a `FREDClient` y a `R2Client`, la fase
LLM publica el bloque genérico con las cifras reales y **no alerta**, por el
tercer eje. La fila queda con `prompt_version` y `validator_approved` en NULL:
no ocurrió ninguna llamada que registrar, y escribir la versión de prompt
afirmaría una que no se hizo.

**El resto de (d) sigue abierto** —FMP, Alpha Vantage, Telegram y las banderas
con un valor inválido—, y no lo resuelve este eje: son componentes necesarios,
así que les corresponde alertar, y el canal de aviso todavía no existe cuando
revientan.
```

- [ ] **Step 4: Actualizar la frase que encabeza las limitaciones**

Es la que va a empezar a mentir, y **el grep del paso siguiente no la caza** porque no contiene la palabra «Anthropic». Reemplazar:

```markdown
**Cuatro limitaciones que hasta ahora no estaban escritas en ningún lado.
Ninguna se arregla acá: la (c) se cerró con el código del 2026-08-25 y queda
como registro, y las otras tres siguen abiertas:**
```

por:

```markdown
**Cuatro limitaciones que hasta ahora no estaban escritas en ningún lado. La
(c) se cerró con el código del 2026-08-25 y la (d) está decidida para Anthropic
desde el 2026-08-26; las dos quedan como registro. La (a), la (b) y el resto de
la (d) siguen abiertas:**
```

Esta es la clase de drift que este mismo ADR documenta haber encontrado dos veces en su propia tabla: no la fila que se toca, sino la frase de más arriba que la resumía.

- [ ] **Step 5: Verificar que ninguna otra mención de Anthropic quedó mintiendo**

Recorrer todo lo que nombra a Anthropic y confirmar contra el código que dice lo que el código hace. Es el mismo ejercicio que destapó las tres divergencias la primera vez y la fila falsa de R2 la segunda.

Run: `grep -n "Anthropic" docs/adr/009-degradation-policy.md`

Expected: **8 líneas** (eran 7 antes de la fila nueva). Tres son filas de tabla —sin key, generador caído, validador— y el resto son menciones en prosa: el bullet de Contexto, la divergencia 1, el párrafo de razones y la limitación (d). Todas tienen que ser coherentes con lo implementado; el conteo solo sirve para confirmar que no se duplicó ni se perdió ninguna.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/009-degradation-policy.md
git commit -m "docs: ADR-009 decide la mitad de Anthropic de la limitacion (d)

La capa LLM sin key no participa: la declaracion que habilita el tercer eje es
ADR-001, que la define como auxiliar. La politica ya se apoyaba en eso para
que la API caida degrade; esta es la otra mitad.

El resto de (d) sigue abierto y se dice por que: son componentes necesarios y
el canal de aviso no existe todavia cuando revientan."
```

---

## Task 7: Suite completa y CI

- [ ] **Step 1: Correr la suite entera**

Run: `./.venv/Scripts/python.exe -m pytest`

Expected: todo verde. Antes de este trabajo eran 165 tests; ahora tienen que ser **172**:

- 3 nuevos en `tests/unit/test_orchestrator_llm.py` — el de `_generic_headline` (Task 1) y los dos de `__init__` por dirección (Task 2).
- 4 nuevos en `tests/integration/test_orchestrator_exit_states.py` — los dos de la run sin capa LLM (Task 3) y los dos de la fila (Task 4).

Si el total no sube exactamente en 7, falta algún test del plan o sobra alguno.

- [ ] **Step 2: Correr mypy y el linter**

Run:
```
./.venv/Scripts/python.exe -m mypy src
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
```

Expected: los tres limpios.

- [ ] **Step 3: Push y verificar la run de CI sobre el HEAD exacto**

```bash
git push
gh run list --limit 3
gh run watch
```

Expected: verde. **"CI configurado" no es "CI pasando" y "verde en local" no es "verde en Actions":** hay que ver el run sobre el SHA exacto que quedó en `main`, no sobre uno anterior.

- [ ] **Step 4: Confirmar la cobertura**

El gate local es `--cov-fail-under=60` y la cobertura real venía en 83.85%, así que el piso no muerde. Confirmar en el log del run que no bajó de forma inesperada; una caída fuerte significaría que algún camino nuevo no tiene test.

---

## Notas para quien ejecute

- **Preferencia de flujo:** Simon trabaja solo en este repo y commitea directo a `main`. No abrir ramas ni PRs salvo que lo pida.
- **No inventar citas a ADRs.** La última vez un plan prescribía «ADR-001 declara opcional al bloque macro» y era falso —ADR-001 no menciona macro ni FRED—, se copió a tres sitios del código y ningún test lo vio. Las citas de este plan son: ADR-001 declara la capa LLM **auxiliar** (título: «LLM fuera del path numérico»); ADR-009 aporta el criterio, los tres ejes y la limitación (d); ADR-007 declara opcional a R2. Verificar cualquier otra antes de escribirla.
- **Un campo nuevo que sale de un `except` y termina en un canal externo hay que redactarlo donde nace.** Acá no aplica —`str(e)` de `LLMClient` es un texto fijo sin credencial— pero es la lección que dejó el fallo de FRED, donde la `api_key` viajaba entera a Telegram dentro del texto de un `HTTPError`.
