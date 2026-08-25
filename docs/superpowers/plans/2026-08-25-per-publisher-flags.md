# Banderas de publicacion por red — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que un fallo de credenciales de una red no impida publicar en la otra,
y que cada red se pueda apagar a proposito con una variable de entorno.

**Architecture:** `publishers_ready` (una bandera para los dos clientes) se
reemplaza por dos clientes opcionales (`XClient | None`, `LinkedInClient | None`)
construidos por separado. La disponibilidad deja de ser un atributo y pasa a ser
una propiedad derivada de `client is not None`, para que ningun test pueda volver
a fingirla sin poner un cliente. El parseo de las banderas y la construccion
tri-estado (listo / apagado / roto) viven en un modulo nuevo y chico,
`publishers/flags.py`, que importan tanto el orquestador como
`scripts/check_publishers.py`.

**Tech Stack:** Python 3.12, pytest 9, `mypy --strict`, `ruff`, structlog.

**Spec:** `docs/superpowers/specs/2026-08-25-per-publisher-flags-design.md`

**Comandos del proyecto** (desde la raiz del repo, PowerShell o bash):

**Usar siempre el interprete del `.venv`, nunca el `python` del PATH.** El Python
global de esta maquina tiene `pandas` y `structlog` pero no `anthropic`,
`playwright` ni `boto3`, y no tiene el paquete instalado en editable. Con el
global, `mypy src` inventa dos errores `no-any-return` (los tipos del SDK de
Anthropic caen a `Any` por `ignore_missing_imports`) y cualquier test que importe
`MacroOrchestrator` ni siquiera colecta. En el `.venv` los mismos comandos dan
`Success: no issues found in 30 source files` y 138 tests en verde.

- Tests rapidos: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q`
- Un test: `./.venv/Scripts/python.exe -m pytest tests/unit/test_publisher_flags.py::test_name -v`
- Tipos: `./.venv/Scripts/python.exe -m mypy src scripts`
- Formato y lint: `./.venv/Scripts/python.exe -m ruff format --check .` y
  `./.venv/Scripts/python.exe -m ruff check .`

Donde el resto del plan diga `pytest`, `mypy` o `ruff` a secas, se entiende
`./.venv/Scripts/python.exe -m <lo que sea>`.

Los contract tests estan deseleccionados por defecto (`addopts = "-m 'not contract'"`);
nada de este plan los toca.

---

### Task 1: El parseo estricto de las banderas

**Files:**
- Create: `src/macro_pipeline/publishers/flags.py`
- Test: `tests/unit/test_publisher_flags.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/unit/test_publisher_flags.py`:

```python
"""Las banderas por red: parseo estricto y construccion tri-estado.

`PUBLISH_X` y `PUBLISH_LINKEDIN` deciden si una red publica. Un parseo laxo
tendria que elegir en silencio entre dos lecturas de `PUBLISH_LINKEDIN=no`
—apagada si se compara contra "true", encendida si se compara contra "false"—
y las dos son malas: una pausa que no pausa, o una pausa que nadie pidio.
"""

import pytest

from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    publisher_enabled,
)


def test_the_default_is_enabled(monkeypatch):
    """Sin la variable, se publica: es el comportamiento de siempre."""
    monkeypatch.delenv(PUBLISH_X_VAR, raising=False)
    assert publisher_enabled(PUBLISH_X_VAR) is True


def test_an_empty_value_is_enabled(monkeypatch):
    """`PUBLISH_X=` es una variable sin decidir, no una red apagada."""
    monkeypatch.setenv(PUBLISH_X_VAR, "")
    assert publisher_enabled(PUBLISH_X_VAR) is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False", "  false  "])
def test_false_in_any_casing_disables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert publisher_enabled(PUBLISH_LINKEDIN_VAR) is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "  true  "])
def test_true_in_any_casing_enables(monkeypatch, value):
    monkeypatch.setenv(PUBLISH_LINKEDIN_VAR, value)
    assert publisher_enabled(PUBLISH_LINKEDIN_VAR) is True


@pytest.mark.parametrize("value", ["no", "0", "off", "yes", "sí"])
def test_anything_else_raises_instead_of_guessing(monkeypatch, value):
    """El valor invalido mata la run en el constructor, y eso es el punto.

    Las dos alternativas son silenciosas: tratarlo como apagada deja de
    publicar sin que nadie lo pida, tratarlo como encendida publica en una red
    que se quiso pausar. Morir con un mensaje claro en la primera run despues
    del typo es lo unico que se ve.
    """
    monkeypatch.setenv(PUBLISH_X_VAR, value)
    with pytest.raises(ValueError) as exc:
        publisher_enabled(PUBLISH_X_VAR)
    assert PUBLISH_X_VAR in str(exc.value)
    assert value in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_publisher_flags.py -v`
Expected: FAIL en la coleccion — `ModuleNotFoundError: No module named 'macro_pipeline.publishers.flags'`.

- [ ] **Step 3: Write minimal implementation**

Crear `src/macro_pipeline/publishers/flags.py`:

```python
"""Banderas de encendido/apagado por red de publicacion.

Vive aparte de `orchestration/main.py` porque lo usan dos consumidores que no
se importan entre si: el orquestador y `scripts/check_publishers.py`. El script
no puede importar el orquestador sin arrastrar pandas, opentelemetry y los
siete clientes.
"""

import os

PUBLISH_X_VAR = "PUBLISH_X"
PUBLISH_LINKEDIN_VAR = "PUBLISH_LINKEDIN"


def publisher_enabled(var: str) -> bool:
    """`True`/`False` desde una variable de entorno, o levanta.

    Ausente o vacia -> True: el default es publicar, que es lo que hacia el
    pipeline antes de que estas banderas existieran.

    Cualquier valor que no sea `true` o `false` levanta `ValueError` a
    proposito. Ver el docstring de `tests/unit/test_publisher_flags.py`: las
    dos formas de adivinar son silenciosas y las dos hacen dano.
    """
    raw = os.environ.get(var, "").strip().lower()
    if not raw:
        return True
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(
        f"{var}={raw!r} no es un valor valido: se espera 'true' o 'false'."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_publisher_flags.py -v`
Expected: PASS, 15 tests (los dos sueltos mas 4 + 4 + 5 parametrizados).

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/publishers/flags.py tests/unit/test_publisher_flags.py
git commit -m "feat(publishers): banderas PUBLISH_X y PUBLISH_LINKEDIN con parseo estricto"
```

---

### Task 2: La construccion tri-estado

Un cliente puede estar **listo**, **apagado a proposito** o **roto**. Las tres
son distintas y el orquestador necesita distinguirlas: la rota alerta, la
apagada no.

**Files:**
- Modify: `src/macro_pipeline/publishers/flags.py`
- Test: `tests/unit/test_publisher_flags.py`

- [ ] **Step 1: Write the failing test**

Agregar `build_publisher` al bloque de imports que ya esta arriba del fichero
(`ruff` con la regla `I` exige los imports agrupados al principio):

```python
from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    build_publisher,
    publisher_enabled,
)
```

Y agregar al final de `tests/unit/test_publisher_flags.py`:

```python
class _Cliente:
    pass


def test_a_healthy_client_comes_back_with_no_error():
    cliente, error = build_publisher("x", _Cliente, enabled=True)
    assert isinstance(cliente, _Cliente)
    assert error is None


def test_a_disabled_publisher_is_not_constructed_at_all():
    """Apagada no es "se construye y no se usa": no se construye.

    Es lo que hace que un token de LinkedIn vencido con la bandera en false
    de una run verde y silenciosa en vez de una degradada con alerta.
    """
    llamadas = []

    def factory():
        llamadas.append(1)
        return _Cliente()

    cliente, error = build_publisher("linkedin", factory, enabled=False)

    assert cliente is None
    assert error is None, "una red apagada no es un fallo y no debe alertar"
    assert llamadas == [], "no se debe ni intentar construir el cliente"


def test_a_broken_client_comes_back_with_the_reason():
    """Rota: sin cliente, pero con el motivo, que es lo que va en la alerta."""

    def factory():
        raise ValueError("Faltan credenciales de X API.")

    cliente, error = build_publisher("x", factory, enabled=True)

    assert cliente is None
    assert error == "Faltan credenciales de X API."


def test_only_valueerror_is_swallowed():
    """Un fallo que no sea de credenciales no se disfraza de red rota."""

    def factory():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        build_publisher("x", factory, enabled=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_publisher_flags.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_publisher'`.

- [ ] **Step 3: Write minimal implementation**

Agregar a `src/macro_pipeline/publishers/flags.py` (los imports nuevos van
arriba del todo, con `import os`):

```python
import os
from collections.abc import Callable
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")
```

Y al final del modulo:

```python
def build_publisher(
    name: str, factory: Callable[[], T], enabled: bool
) -> tuple[T | None, str | None]:
    """Construye un cliente de publicacion, devolviendo (cliente, motivo).

    Las tres combinaciones son distintas y el orquestador las distingue:

    - `(cliente, None)` — listo.
    - `(None, None)` — apagado a proposito: se loggea y nada mas. **No alerta**:
      una decision tuya no es un fallo, y alertar cada semana por una pausa
      deliberada es lo que hace que se dejen de leer las alertas.
    - `(None, motivo)` — roto: falta alguna credencial. El motivo es el texto
      del `ValueError` del cliente y termina en la alerta de Telegram.

    Solo se atrapa `ValueError`, que es lo que levantan `XClient` y
    `LinkedInClient` cuando falta una credencial. Cualquier otra excepcion sale
    y mata la run: no es una red rota, es un bug.
    """
    if not enabled:
        logger.info("publisher_disabled", publisher=name)
        return None, None
    try:
        return factory(), None
    except ValueError as e:
        logger.warning("publisher_not_configured", publisher=name, reason=str(e))
        return None, str(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_publisher_flags.py -v`
Expected: PASS, 19 tests.

Run: `mypy src`
Expected: `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/publishers/flags.py tests/unit/test_publisher_flags.py
git commit -m "feat(publishers): construccion tri-estado listo/apagado/roto"
```

---

### Task 3: Cablear las dos banderas en el orquestador

Este task cambia el constructor y la guarda pre-lock en el mismo commit, porque
la guarda lee `publishers_ready` y ese atributo desaparece: separarlos deja el
repo sin compilar entre dos commits.

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:89-95` (construccion)
- Modify: `src/macro_pipeline/orchestration/main.py:228-252` (guarda pre-lock)
- Modify: `tests/integration/test_orchestrator_exit_states.py:44-70,157-165`
- Modify: `tests/integration/test_orchestrator_persistence.py:37-55,144-197`

- [ ] **Step 1: Write the failing test**

En `tests/integration/test_orchestrator_exit_states.py`, reemplazar en
`_build_orchestrator` las lineas

```python
    orch.publishers_ready = True
    orch.x_client = MagicMock()
```

por

```python
    orch.x_enabled = True
    orch.linkedin_enabled = True
    orch.x_error = None
    orch.linkedin_error = None
    orch.x_client = MagicMock()
```

(el resto del fixture no cambia). Hacer el mismo reemplazo en
`tests/integration/test_orchestrator_persistence.py`, borrando ademas el
comentario de cuatro lineas que empieza en "Con publicadores: el camino normal"
y dejando este en su lugar:

```python
    # Con publicadores: el camino normal. `x_ready` / `linkedin_ready` son
    # propiedades de solo lectura derivadas del cliente, asi que declarar una
    # red lista obliga a poner un cliente. Es a proposito: el atajo de setear
    # la bandera a mano es como el bug de `5ba7997` estuvo escondido detras de
    # cuatro tests verdes.
```

Reemplazar `test_a_run_without_publishers_leaves_no_row_at_all` en
`test_orchestrator_exit_states.py` por estos tres:

```python
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
    orch.x_enabled = False
    orch.x_client = None
    orch.linkedin_enabled = False
    orch.linkedin = None

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}
    orch.telegram.send_alert.assert_not_called()
    orch.telegram.send_approval_request.assert_not_called()


def test_one_disabled_and_one_broken_alerts_only_about_the_broken_one(data, state):
    """No publica nadie, asi que aborta; pero solo una de las dos es un fallo."""
    orch = _build_orchestrator(data, state)
    orch.x_enabled = False
    orch.x_client = None
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID) == {}
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "LinkedIn" in aviso
    assert "X:" not in aviso, "una red apagada no se menciona como fallo"
```

En `test_orchestrator_persistence.py`, en los dos tests que hoy setean
`orch.publishers_ready = False`
(`test_a_run_without_publishers_is_not_marked_as_published` y
`test_a_run_without_publishers_does_not_bother_the_operator`), reemplazar esa
linea por:

```python
    orch.x_client = None
    orch.x_error = "Faltan credenciales de X API."
    orch.linkedin = None
    orch.linkedin_error = "Faltan credenciales de LinkedIn."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration -v`
Expected: FAIL. Los tests nuevos fallan porque `run_weekly_close` sigue leyendo
`self.publishers_ready`, que ya no lo setea ningun fixture: `AttributeError:
'MacroOrchestrator' object has no attribute 'publishers_ready'`.

- [ ] **Step 3: Write minimal implementation**

3a. En `src/macro_pipeline/orchestration/main.py`, agregar el import junto a los
otros de `macro_pipeline.publishers`:

```python
from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    build_publisher,
    publisher_enabled,
)
```

3b. Reemplazar el bloque de construccion (hoy lineas 89-95):

```python
        try:
            self.x_client = XClient()
            self.linkedin = LinkedInClient()
            self.publishers_ready = True
        except ValueError as e:
            logger.warning("publishers_not_configured", reason=str(e))
            self.publishers_ready = False
```

por:

```python
        # Una bandera por red, y no una para las dos: `XClient` levanta si
        # falta alguna de sus cuatro credenciales y `LinkedInClient` si falta
        # alguna de sus dos, asi que un solo `try` compartido dejaba que
        # cualquiera de las seis apagara las dos redes.
        self.x_enabled = publisher_enabled(PUBLISH_X_VAR)
        self.linkedin_enabled = publisher_enabled(PUBLISH_LINKEDIN_VAR)
        self.x_client, self.x_error = build_publisher(
            "x", XClient, self.x_enabled
        )
        self.linkedin, self.linkedin_error = build_publisher(
            "linkedin", LinkedInClient, self.linkedin_enabled
        )
```

3c. Agregar las dos propiedades justo despues de `__init__` (antes de
`_fetch_macro_snapshot`):

```python
    @property
    def x_ready(self) -> bool:
        """Derivada del cliente y no un atributo aparte, a proposito.

        Un atributo puede desincronizarse del cliente, y peor: un test puede
        ponerlo a mano para saltearse el mockeo. Es exactamente como el bug de
        `5ba7997` —publicar nada y marcar el evento como publicado— vivio
        detras de cuatro tests de integracion en verde.
        """
        return self.x_client is not None

    @property
    def linkedin_ready(self) -> bool:
        return self.linkedin is not None

    def _publisher_failures(self) -> str:
        """Las redes rotas y su motivo, para el texto de la alerta.

        Una red apagada no aparece: no tiene motivo porque no es un fallo.
        """
        fallos = []
        if self.x_error:
            fallos.append(f"X: {self.x_error}")
        if self.linkedin_error:
            fallos.append(f"LinkedIn: {self.linkedin_error}")
        return "\n".join(fallos)
```

3d. Reemplazar la guarda pre-lock entera (hoy lineas 228-252, desde el
comentario `# ── Sin publicadores no hay cierre semanal ──` hasta el `return`)
por:

```python
                # ── Sin ninguna red no hay cierre semanal ──────────────────────
                # Va antes del lock y antes de tocar el estado: si no se puede
                # publicar, todo lo que sigue —renderizar, llamar al LLM, pedir
                # aprobación a un humano— es trabajo tirado, y terminaba peor
                # que tirado. `mark_as_published` estaba al mismo nivel que el
                # `if self.publishers_ready`, no dentro, así que la run se
                # cerraba marcando como publicado un evento que no se publicó
                # en ninguna red (`5ba7997`).
                # Basta con **una** red viva: que falte la credencial de una no
                # es motivo para no publicar en la otra (ADR-009 — se degrada
                # cuando el fallo solo cuesta contexto).
                # No se toca el estado a propósito: sin fila, la próxima run
                # reintenta sola.
                if not (self.x_ready or self.linkedin_ready):
                    if not self.x_enabled and not self.linkedin_enabled:
                        # Las dos apagadas a propósito: no hay nada que avisar.
                        logger.info("no_publishers_enabled", event_id=event_id)
                        return
                    logger.error("publishers_not_ready_aborting", event_id=event_id)
                    self.telegram.send_alert(
                        "⚠️ El cierre semanal no se ejecutó: no hay ninguna red "
                        "en condiciones de publicar.\n\n"
                        f"{self._publisher_failures()}\n\n"
                        "No se publicó nada y el evento queda sin marcar, así "
                        "que la próxima run lo reintenta. Verificar con "
                        "`python scripts/check_publishers.py`."
                    )
                    return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit tests/integration -v`
Expected: PASS. La fase de publicacion todavia dice `if self.publishers_ready:`
(linea ~432), asi que **este paso va a fallar ahi** con `AttributeError`. Para
que este task cierre verde, aplicar tambien el cambio minimo de esa linea:
reemplazar `if self.publishers_ready:` por `if self.x_ready or self.linkedin_ready:`.
El desglose por red es el Task 4.

Run: `mypy src` -> `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/
git commit -m "refactor(orchestration): una bandera de publicacion por red"
```

---

### Task 4: La fase de publicacion por red, y la alerta de degradacion

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:384` (alerta, antes del HITL)
- Modify: `src/macro_pipeline/orchestration/main.py:432-461` (fase de publicacion)
- Test: `tests/integration/test_orchestrator_exit_states.py`

- [ ] **Step 1: Write the failing test**

Agregar a `tests/integration/test_orchestrator_exit_states.py`:

```python
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
    assert llamadas.index("send_alert") < llamadas.index("send_approval_request")
    aviso = orch.telegram.send_alert.call_args[0][0]
    assert "LinkedIn" in aviso
    assert "Faltan credenciales de LinkedIn." in aviso


def test_a_disabled_network_never_warns(data, state):
    """Apagada a proposito con la otra viva: publica y no dice nada.

    Es el caso del token de LinkedIn venciendo en octubre: con la bandera en
    false la run es verde y silenciosa en vez de degradada con alerta.
    """
    orch = _build_orchestrator(data, state)
    orch.linkedin_enabled = False
    orch.linkedin = None

    orch.run_weekly_close()

    assert state.get_publication_state(EVENT_ID)["status"] == "published"
    orch.telegram.send_alert.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_orchestrator_exit_states.py -v`
Expected: FAIL. `test_a_broken_linkedin_still_publishes_on_x` muere con
`AttributeError: 'NoneType' object has no attribute 'post_text'`, porque la fase
de publicacion todavia llama a los dos clientes sin mirar cual existe.
`test_the_degraded_run_warns_before_asking_for_approval` falla con `ValueError:
'send_alert' is not in list`.

- [ ] **Step 3: Write minimal implementation**

4a. Insertar la alerta de degradacion **inmediatamente antes** de la linea
`                # ── FASE HITL ──────────────────────────────────────────────────`
(hoy 384), con 16 espacios de indentacion:

```python
                # ── Degradación: una red rota y la otra viva ───────────────────
                # Va antes de pedir aprobación, igual que el aviso de la capa
                # LLM y por el mismo motivo: quien aprueba tiene que saber que
                # ese cierre sale en una sola red.
                # Una red *apagada* no llega acá con `error` cargado: apagarla
                # es una decisión, no un fallo, y alertar cada semana por una
                # decisión propia es el ruido que hace que se dejen de leer las
                # alertas. Si llega una alerta, es porque algo se rompió.
                # Acá hay como mucho un error: si las dos estuvieran rotas, la
                # guarda pre-lock ya habría abortado.
                if self.x_error or self.linkedin_error:
                    caida = "X" if self.x_error else "LinkedIn"
                    viva = "LinkedIn" if self.x_error else "X"
                    logger.warning("publisher_degraded", down=caida, up=viva)
                    self.telegram.send_alert(
                        f"⚠️ El cierre semanal sale solo en {viva}: el cliente "
                        f"de {caida} no se pudo construir.\n\n"
                        f"Motivo: {self.x_error or self.linkedin_error}\n\n"
                        "El cierre se publica igual. Verificar con "
                        "`python scripts/check_publishers.py`."
                    )
```

4b. Reemplazar el bloque de publicacion entero (hoy 432-461, desde
`                    if self.x_ready or self.linkedin_ready:` hasta el cierre del
`else` de LinkedIn) por, con 20 espacios de indentacion en el `with`:

```python
                    with (
                        self.tracer.start_as_current_span("publish")
                        if self.tracer
                        else nullcontext()
                    ):
                        # Cada red mira su propio cliente. `is not None` y no
                        # `self.x_ready` porque `mypy --strict` estrecha el tipo
                        # con lo primero y no con lo segundo.
                        # La reconciliación por `post_id` no se reemplaza: se
                        # combina. Una red puede estar sin publicar por dos
                        # motivos distintos —no está lista, o ya salió en un
                        # intento anterior— y son independientes.
                        if self.x_client is None:
                            logger.info("x_not_publishing", event_id=event_id)
                        elif x_already_done:
                            logger.info(
                                "x_already_published_skipping", event_id=event_id
                            )
                        else:
                            x_result = self.x_client.post_tweet(headline)
                            x_post_id = x_result.get("data", {}).get("id", "unknown")
                            self.state.mark_x_published(event_id, x_post_id)

                        if self.linkedin is None:
                            logger.info("linkedin_not_publishing", event_id=event_id)
                        elif linkedin_already_done:
                            logger.info(
                                "linkedin_already_published_skipping",
                                event_id=event_id,
                            )
                        else:
                            li_result = self.linkedin.post_text(headline)
                            li_post_id = li_result.get("id", "unknown")
                            self.state.mark_linkedin_published(event_id, li_post_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit tests/integration -v`
Expected: PASS, todo.

Run: `mypy src` -> `Success`. Run: `ruff check .` y `ruff format --check .` -> sin cambios.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(orchestration): que una red rota no impida publicar en la otra"
```

---

### Task 5: `check_publishers.py` mira las banderas

**Files:**
- Modify: `scripts/check_publishers.py`
- Test: `tests/unit/test_check_publishers.py`

- [ ] **Step 1: Write the failing test**

Agregar a `tests/unit/test_check_publishers.py`:

```python
def test_a_disabled_network_cannot_turn_the_script_red(check_publishers, monkeypatch, capsys):
    """El codigo de salida significa "las credenciales de publicacion sirven".

    Una red apagada no tiene credenciales que sirvan ni que dejen de servir: no
    participa. Un gate que se pone rojo por una decision tomada a proposito
    termina desactivado, que es el mismo motivo por el que la deriva de `.env`
    informa en vez de bloquear.
    """
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setenv("PUBLISH_LINKEDIN", "false")
    monkeypatch.setattr(check_publishers, "report_env_drift", lambda *a, **k: None)

    assert check_publishers.main() == 0

    salida = capsys.readouterr().out
    assert "apagada" in salida.lower()


def test_a_disabled_network_is_not_even_checked(check_publishers, monkeypatch):
    """No se contacta la API de una red que no se va a usar.

    Se mockea `check_x` y no `requests.get`: `check_x` autentica con
    `OAuth1Session.get`, asi que un test que vigile `requests.get` pasaria
    igual con el bug puesto.
    """
    llamadas = []
    monkeypatch.setenv("PUBLISH_X", "false")
    monkeypatch.setattr(check_publishers, "report_env_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        check_publishers, "check_x", lambda: llamadas.append("x") or True
    )
    monkeypatch.setattr(check_publishers, "check_linkedin", lambda: True)

    check_publishers.main()

    assert llamadas == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_check_publishers.py -v`
Expected: FAIL — `main()` devuelve 1 y la salida no menciona "apagada".

- [ ] **Step 3: Write minimal implementation**

5a. Agregar el import al bloque de imports de `scripts/check_publishers.py`,
despues de `from requests_oauthlib import OAuth1Session`:

```python
from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    publisher_enabled,
)
```

5b. Reemplazar `main()` entera por:

```python
def main() -> int:
    print("Verificación de credenciales de publicación (no publica nada).")
    report_env_drift(ROOT / ".env.example", ROOT / ".env")

    x_on = publisher_enabled(PUBLISH_X_VAR)
    linkedin_on = publisher_enabled(PUBLISH_LINKEDIN_VAR)

    # Una red apagada no se chequea y no cuenta para el código de salida: no
    # tiene credenciales que sirvan ni que dejen de servir, porque no publica.
    if x_on:
        x_ok = check_x()
    else:
        print("\n-- X ----------------------------------------------")
        print(f"{OK} Apagada por {PUBLISH_X_VAR}=false: no se verifica.")
        x_ok = True

    if linkedin_on:
        linkedin_ok = check_linkedin()
    else:
        print("\n-- LinkedIn ---------------------------------------")
        print(f"{OK} Apagada por {PUBLISH_LINKEDIN_VAR}=false: no se verifica.")
        linkedin_ok = True

    print("\n-- Resultado --------------------------------------")
    print(f"X:        {'apagada' if not x_on else 'listo' if x_ok else 'NO listo'}")
    print(
        f"LinkedIn: "
        f"{'apagada' if not linkedin_on else 'listo' if linkedin_ok else 'NO listo'}"
    )
    if not x_on and not linkedin_on:
        print("\nLas dos redes están apagadas: el pipeline no va a publicar")
        print("en ninguna parte y aborta antes de tocar el estado.")
        return 0
    if x_ok and linkedin_ok:
        print("\nLa publicación real puede ejercitarse de punta a punta en las")
        print("redes encendidas.")
        return 0
    return 1
```

5c. Actualizar el docstring del modulo: la frase "con los placeholders de
`.env.example` cargados, `publishers_ready` puede quedar en True" ya no aplica.
Reemplazar esa oracion por:

```
El pipeline solo comprueba que las variables *existan*: con los placeholders de
`.env.example` cargados una red queda marcada como lista y el fallo aparece
recién después de que un humano aprobó el post en Telegram. Este script hace la
pregunta que importa —¿estas credenciales sirven para publicar?— contra
endpoints de solo lectura. Una red apagada con `PUBLISH_X=false` o
`PUBLISH_LINKEDIN=false` no se verifica y no afecta el código de salida.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_check_publishers.py -v`
Expected: PASS.

Run: `mypy src scripts` -> `Success`.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_publishers.py tests/unit/test_check_publishers.py
git commit -m "feat(scripts): que una red apagada no ponga el chequeo en rojo"
```

---

### Task 6: Declarar las dos variables en `.env` y `.env.example`

**Files:**
- Modify: `.env` (no versionado)
- Modify: `.env.example`
- Test: `tests/unit/test_check_publishers.py`

- [ ] **Step 1: Write the failing test**

Agregar a `tests/unit/test_check_publishers.py`, junto a
`test_the_example_keeps_the_two_decided_declarations`:

```python
def test_the_example_declares_both_publish_flags(check_publishers):
    """Explícitas y no heredadas del default, igual que `ALLOW_MOCK_DATA`.

    Deciden si se publica. Una bandera que decide eso no debería depender de un
    default del código: hay que poder leer el `.env` y saber qué va a pasar.
    """
    ejemplo = check_publishers._parse_env_file(ROOT / ".env.example")

    assert ejemplo.get("PUBLISH_X") == "true"
    assert ejemplo.get("PUBLISH_LINKEDIN") == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_check_publishers.py::test_the_example_declares_both_publish_flags -v`
Expected: FAIL — `assert None == 'true'`.

- [ ] **Step 3: Write minimal implementation**

Agregar a **los dos** ficheros, `.env.example` y `.env`, justo antes del bloque
`# --- Seguridad de datos ---`:

```
# --- Publicacion por red ---
# true/false estrictos: cualquier otro valor mata la run en el constructor en
# vez de elegir en silencio entre una pausa que no pausa y una pausa que nadie
# pidio. Una red apagada no se construye, no publica, no alerta y no la
# verifica scripts/check_publishers.py: es una decision, no un fallo.
PUBLISH_X=true
PUBLISH_LINKEDIN=true
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit -v`
Expected: PASS, incluido el test de deriva, que ahora ve las dos variables en
los dos ficheros.

Run: `python scripts/check_publishers.py`
Expected: sin deriva reportada, y las dos redes verificadas como hasta ahora.
Despues probar a mano `PUBLISH_LINKEDIN=false python scripts/check_publishers.py`
(en PowerShell: `$env:PUBLISH_LINKEDIN="false"; python scripts/check_publishers.py`)
y verificar que LinkedIn sale como "apagada" y el codigo de salida es 0.

- [ ] **Step 5: Commit**

```bash
git add .env.example tests/unit/test_check_publishers.py
git commit -m "chore(env): declarar PUBLISH_X y PUBLISH_LINKEDIN explicitas"
```

(`.env` no se commitea: esta en `.gitignore`.)

---

### Task 7: Actualizar ADR-009 y el docstring del orquestador

**Files:**
- Modify: `docs/adr/009-degradation-policy.md`
- Modify: `src/macro_pipeline/orchestration/main.py:46-56` (docstring de clase)

- [ ] **Step 1: Actualizar la tabla de politica por componente**

En `docs/adr/009-degradation-policy.md`, reemplazar estas dos filas:

```
| X / LinkedIn | Credenciales ausentes | **Aborta** antes del lock, con alerta | Ninguna fila |
| X / LinkedIn | Publicación fallida | **Aborta** — `post_id` de lo que sí salió persistido | `failed` |
```

por estas cuatro:

```
| X / LinkedIn | Credenciales ausentes en **una** de las dos | **Degrada** — publica en la otra, con alerta antes de pedir aprobación | — |
| X / LinkedIn | Credenciales ausentes en **las dos** | **Aborta** antes del lock, con alerta | Ninguna fila |
| X / LinkedIn | Apagada con `PUBLISH_X` / `PUBLISH_LINKEDIN` en `false` | **No es un fallo** — no se construye, no publica y **no alerta** | — |
| X / LinkedIn | Publicación fallida | **Aborta** — `post_id` de lo que sí salió persistido | `failed` |
```

- [ ] **Step 2: Agregar la decision a la seccion de razones explicitas**

Despues del parrafo que empieza con "**Toda excepción marca `failed`.**",
agregar:

```markdown
**Una red de publicación caída degrada; solo aborta si no queda ninguna.**
Es lo que el criterio ya predice: que falte la credencial de LinkedIn no hace
que ninguna cifra sea incorrecta y no impide publicar — impide publicar *en una
red*. Hasta el 2026-08-25 una sola bandera `publishers_ready` cubría los dos
clientes, así que un `ValueError` de cualquiera de las seis credenciales apagaba
las dos. La run degradada termina en `published`, no en `failed`: fue un éxito
con menos alcance. Se descartó publicar en una red y dejar la fila `failed` para
reintentar la otra, porque el `event_id` lleva la fecha y el reintento solo
reconcilia el mismo día: al día siguiente republicaría en la red que sí había
salido.

**Un apagado deliberado no alerta.** `PUBLISH_X=false` y `PUBLISH_LINKEDIN=false`
apagan una red a propósito: no se construye el cliente, no se publica, y no se
manda nada a Telegram. Es la única excepción a la regla de que toda degradación
alerta, y la razón es la misma que sostiene la regla: alertar existe para que
una publicación degradada no pase inadvertida, y una decisión propia no pasa
inadvertida. Un aviso semanal por una pausa que pediste es el ruido que hace que
se deje de leer el aviso que importa. La distinción que queda fijada es **si
llega una alerta, es porque algo se rompió**.
```

- [ ] **Step 3: Actualizar el docstring de `MacroOrchestrator`**

Reemplazar la linea

```
    - Idempotencia parcial: si X publicó pero LinkedIn falló, el re-run
      solo publica LinkedIn.
```

por

```
    - Idempotencia parcial: si X publicó pero LinkedIn falló, el re-run
      solo publica LinkedIn (mismo día: el `event_id` lleva la fecha).
    - Una red por bandera: si falta una credencial de X, LinkedIn publica
      igual, y al revés. `PUBLISH_X` / `PUBLISH_LINKEDIN` apagan una red a
      propósito, en silencio.
```

- [ ] **Step 4: Verificar**

Run: `pytest tests/unit tests/integration -q` -> todo verde.
Run: `mypy src scripts` -> `Success`.
Run: `grep -rn "publishers_ready" src scripts tests docs` -> **sin resultados**
salvo las menciones historicas de ADR-009 y de los docstrings de tests que
explican el bug de `5ba7997`.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/009-degradation-policy.md src/macro_pipeline/orchestration/main.py
git commit -m "docs: ADR-009 con la politica por red de publicacion"
```

---

## Verificacion final

- [ ] `pytest tests/unit tests/integration -q` — todo verde, sin skips nuevos.
- [ ] `pytest --cov=src --cov-fail-under=60 -q` — la cobertura no baja.
- [ ] `mypy src scripts` — `Success: no issues found`.
- [ ] `ruff check .` y `ruff format --check .` — limpio.
- [ ] `python scripts/check_publishers.py` — sale 0 con las dos redes encendidas.
- [ ] Con `PUBLISH_LINKEDIN=false`: el script dice "apagada" y sale 0.
- [ ] `git push` y **mirar el run de CI**, no solo el verde local. Es la
      leccion que dejo el gate muerto de Codecov: "CI configurado" no es "CI
      pasando", y "verde en local" no predice "verde en Actions".
