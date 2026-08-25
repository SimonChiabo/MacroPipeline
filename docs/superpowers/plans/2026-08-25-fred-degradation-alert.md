# Alerta de FRED y eje de lo opcional — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el pipeline avise cuando el bloque macro se cae por un fallo, y que
siga callado cuando FRED simplemente no esta configurado.

**Architecture:** el motivo del fallo viaja desde donde se atrapa la excepcion
(`safe_build_macro_snapshot`) hasta la alerta, guardado en `self.macro_error` —
la misma forma que `self.x_error` / `self.linkedin_error`. Las tres causas
silenciosas de hoy se separan en dos clases: fallo (alerta, con la causa real) y
opcional sin configurar (silencio). ADR-009 gana un segundo eje que explica de
una vez a FRED, R2 y los publicadores.

**Tech Stack:** Python 3.12, pytest 9, `mypy --strict`, `ruff`, structlog.

**Spec:** `docs/superpowers/specs/2026-08-25-fred-degradation-alert-design.md`

**Comandos del proyecto — usar siempre el interprete del `.venv`, nunca el
`python` del PATH.** El Python global de esta maquina no tiene `anthropic`,
`playwright` ni `boto3` ni el paquete en editable: con el, `mypy src` inventa
errores y los tests que importan el orquestador ni colectan.

- Tests: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q`
- Tipos: `./.venv/Scripts/python.exe -m mypy src scripts`
- Lint: `./.venv/Scripts/python.exe -m ruff check .` y `-m ruff format --check .`

**Baselines a no regresar:** 153 passed, `Success: no issues found in 30 source
files`, ruff limpio.

---

### Task 1: `safe_build_macro_snapshot` devuelve el motivo

**Files:**
- Modify: `src/macro_pipeline/data/macro.py`
- Test: `tests/unit/test_macro.py`

- [ ] **Step 1: Actualizar los tres tests existentes a la firma nueva**

En `tests/unit/test_macro.py`, reemplazar los tres tests de
`safe_build_macro_snapshot` por estos. No son solo un cambio de firma: los dos
de fallo ahora exigen que **llegue el motivo**, que es lo unico que puede
nombrar la causa real en la alerta.

```python
def test_safe_build_macro_snapshot_returns_none_when_fred_fails():
    """El bloque macro es complementario: si FRED falla, el pipeline sigue sin él."""

    class BoomFred:
        def get_series_observations(self, series_id, **kwargs):
            raise FREDClientError("FRED caído")

    snapshot, motivo = safe_build_macro_snapshot(BoomFred(), today=date(2026, 8, 9))

    assert snapshot is None
    assert motivo is not None
    assert "FRED caído" in motivo, "el motivo tiene que llevar el error real"
    assert "FREDClientError" in motivo, "y el tipo, para saber dónde mirar"


def test_safe_build_macro_snapshot_returns_none_on_insufficient_history():
    short = FakeFred(
        {
            CPI_SERIES: _series([("2026-06-01", 309.0)]),
            UNRATE_SERIES: _series([("2026-07-01", 4.1)]),
            DGS10_SERIES: _series([("2026-08-06", 4.69)]),
        }
    )

    snapshot, motivo = safe_build_macro_snapshot(short, today=date(2026, 8, 9))

    assert snapshot is None
    assert motivo is not None


def test_the_reason_distinguishes_a_dead_fred_from_a_short_series():
    """Dos fallos distintos no pueden dar el mismo aviso.

    Es la leccion de `f53a755`: la alerta de la capa LLM culpaba al prompt
    tambien cuando lo que moria era la API, y mandaba a revisar un prompt sano.
    """

    class BoomFred:
        def get_series_observations(self, series_id, **kwargs):
            raise FREDClientError("FRED caído")

    short = FakeFred(
        {
            CPI_SERIES: _series([("2026-06-01", 309.0)]),
            UNRATE_SERIES: _series([("2026-07-01", 4.1)]),
            DGS10_SERIES: _series([("2026-08-06", 4.69)]),
        }
    )

    _, motivo_caido = safe_build_macro_snapshot(BoomFred(), today=date(2026, 8, 9))
    _, motivo_corto = safe_build_macro_snapshot(short, today=date(2026, 8, 9))

    assert motivo_caido != motivo_corto


def test_safe_build_macro_snapshot_returns_snapshot_on_success(fake_fred):
    snap, motivo = safe_build_macro_snapshot(fake_fred, today=date(2026, 8, 9))

    assert snap is not None
    assert snap.unemployment_rate == pytest.approx(4.1)
    assert motivo is None, "el camino feliz no deja motivo cargado"
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_macro.py -v`
Expected: FAIL — `TypeError: cannot unpack non-sequence MacroSnapshot` en el
test de exito, y `TypeError: cannot unpack non-sequence NoneType` en los de
fallo.

- [ ] **Step 3: Cambiar la firma**

En `src/macro_pipeline/data/macro.py`, reemplazar `safe_build_macro_snapshot`
entera por:

```python
def safe_build_macro_snapshot(
    fred: SeriesProvider, today: date | None = None
) -> tuple[MacroSnapshot | None, str | None]:
    """
    Versión tolerante a fallos: devuelve `(snapshot, motivo)` en vez de propagar.

    El bloque macro es complementario al cierre de mercado. Que FRED esté caído
    o que una serie venga corta no debe abortar la publicación semanal.

    El motivo se devuelve además de loggearse porque es lo único que puede
    nombrar la causa real en la alerta de Telegram, y se arma acá porque acá es
    donde la excepción existe. Un aviso que no distingue "FRED caído" de "serie
    corta" manda a mirar el lugar equivocado — misma lección que `f53a755`.
    """
    try:
        return build_macro_snapshot(fred, today=today), None
    except Exception as e:
        motivo = f"{type(e).__name__}: {e}"
        logger.warning(
            "macro_snapshot_unavailable", error=str(e), error_type=type(e).__name__
        )
        return None, motivo
```

- [ ] **Step 4: Verificar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_macro.py -v` → PASS.

Run: `./.venv/Scripts/python.exe -m mypy src scripts`
Expected: **errores en `orchestration/main.py` y en ningun otro fichero**, porque
el unico caller sigue tratando el retorno como un `MacroSnapshot` (lo pasa a
`validate_macro_snapshot` y lo devuelve como `MacroSnapshot | None`, asi que
probablemente sean dos y no uno — el numero exacto no importa, el fichero si).
Los cierra el Task 2. **No arreglarlos aca** con `# type: ignore`, ni con un
`cast`, ni desempaquetando a medias: dejar el rojo es lo que garantiza que el
Task 2 tenga que tocar el caller.

Run: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q`
Expected: los tests de `tests/unit/test_macro.py` pasan; los del orquestador que
ejerciten `_fetch_macro_snapshot` pueden fallar. Anotar cuales y seguir.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/data/macro.py tests/unit/test_macro.py
git commit -m "refactor(data): que safe_build_macro_snapshot devuelva el motivo"
```

Terminar el cuerpo del mensaje con:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JCHkepwf6fNKTS4gnbvZPj
```

---

### Task 2: `_fetch_macro_snapshot` separa fallo de no-configurado

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Test: `tests/unit/test_orchestrator_macro.py` (nuevo)

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/unit/test_orchestrator_macro.py`:

```python
"""Las tres causas por las que el bloque macro no llega, y cual es un fallo.

`_fetch_macro_snapshot` devolvia `None` para las tres sin distinguirlas, y las
tres solo loggeaban. Dos son fallos y una es una configuracion: ADR-001 declara
opcional al bloque macro, y un opcional sin configurar no participa — no
degrada. Avisar todas las semanas de una configuracion permanente es el ruido
que hace que se deje de leer el aviso que importa.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.validators.engine import ValidationError
from macro_pipeline.validators.schemas import MacroSnapshot


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


def _orchestrator(fred):
    """Solo las dos piezas que `_fetch_macro_snapshot` toca."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.fred = fred
    orch.macro_error = None
    orch.validator_engine = MagicMock()
    return orch


def test_fred_without_a_key_is_not_a_failure():
    """Sin key no hay motivo: es una configuracion, no algo que se rompio."""
    orch = _orchestrator(None)

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error is None


def test_a_dead_fred_is_a_failure_and_carries_the_reason(monkeypatch):
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error == "FREDClientError: FRED caído"


def test_a_rejected_figure_is_a_failure_and_says_so(monkeypatch, snapshot):
    """La mas alarmante de las tres y hoy la mas silenciosa.

    Que el validador rechace significa que FRED devolvio una cifra fuera de
    rango de plausibilidad, que es la clase de dato que no se quiere cerca de
    una publicacion financiera.
    """
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    orch.validator_engine.validate_macro_snapshot.side_effect = ValidationError(
        "cpi_yoy=0.9 fuera de rango"
    )

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error is not None
    assert "validador" in orch.macro_error.lower()
    assert "cpi_yoy=0.9 fuera de rango" in orch.macro_error


def test_the_reason_distinguishes_the_two_failures(monkeypatch, snapshot):
    """Un aviso que no distingue las dos causas manda a mirar donde no es."""
    caido = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )
    caido._fetch_macro_snapshot()

    rechazado = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    rechazado.validator_engine.validate_macro_snapshot.side_effect = ValidationError(
        "cpi_yoy=0.9 fuera de rango"
    )
    rechazado._fetch_macro_snapshot()

    assert caido.macro_error != rechazado.macro_error


def test_the_happy_path_leaves_no_reason(monkeypatch, snapshot):
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )

    assert orch._fetch_macro_snapshot() is snapshot
    assert orch.macro_error is None


def test_a_retry_does_not_inherit_the_previous_reason(monkeypatch, snapshot):
    """Sin limpiar, una run que reintenta avisa de algo ya resuelto."""
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )
    orch._fetch_macro_snapshot()
    assert orch.macro_error is not None

    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    orch._fetch_macro_snapshot()

    assert orch.macro_error is None
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_macro.py -v`
Expected: FAIL. `test_a_dead_fred_is_a_failure_and_carries_the_reason` falla con
`AssertionError` sobre `macro_error` (queda en `None`), y los que llegan a
desempaquetar fallan con `TypeError: cannot unpack non-sequence`.

- [ ] **Step 3: Implementar**

3a. En `src/macro_pipeline/orchestration/main.py`, dentro de `__init__`,
inmediatamente despues del bloque que construye `self.fred` (el `try/except
ValueError` que loggea `fred_not_configured`), agregar:

```python
        # Motivo por el que el bloque macro no llegó, o None. Mismo rol que
        # `x_error` / `linkedin_error`: lo escribe quien detecta el fallo y lo
        # lee la alerta. Declarado acá para que exista desde el minuto cero.
        self.macro_error: str | None = None
```

3b. Reemplazar `_fetch_macro_snapshot` entera por:

```python
    def _fetch_macro_snapshot(self) -> MacroSnapshot | None:
        """
        Obtiene y valida el contexto macro de FRED.

        Devuelve None ante cualquier problema (sin key, API caída, serie corta,
        dato rancio o fuera de rango). El cierre semanal se publica igual: los
        índices son el contenido principal, el macro es contexto.

        Escribe `self.macro_error` con el motivo cuando el fallo **es** un
        fallo, y lo deja en None cuando FRED simplemente no está configurado:
        ADR-001 declara opcional al bloque macro, y un componente opcional que
        no participa no está degradando. Avisar cada semana de una
        configuración permanente es el ruido que hace que se deje de leer el
        aviso que importa.
        """
        # Se limpia siempre, y no solo se escribe en los caminos malos: un
        # reintento dentro del mismo proceso heredaría el motivo de la run
        # anterior y avisaría de algo ya resuelto.
        self.macro_error = None

        if self.fred is None:
            return None

        snapshot, motivo = safe_build_macro_snapshot(self.fred)
        if snapshot is None:
            self.macro_error = motivo
            return None

        try:
            self.validator_engine.validate_macro_snapshot(snapshot)
        except ValidationError as e:
            logger.warning("macro_snapshot_rejected_by_validator", reason=str(e))
            self.macro_error = f"El validador rechazó la cifra macro: {e}"
            return None

        return snapshot
```

- [ ] **Step 4: Verificar**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_macro.py -v` → PASS, 6 tests.

Run: `./.venv/Scripts/python.exe -m mypy src scripts`
Expected: `Success: no issues found in 30 source files`. Los errores que dejo el
Task 1 se cierran aca. El conteo sigue en 30 y no sube por el fichero de test
nuevo, porque `mypy src scripts` no mira `tests/`. Si dice otra cosa, parar y
reportar.

Run: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q` → todo verde.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/unit/test_orchestrator_macro.py
git commit -m "feat(orchestration): distinguir FRED roto de FRED sin configurar"
```

Con los dos trailers de siempre.

---

### Task 3: La alerta

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py`
- Modify: `tests/integration/test_orchestrator_exit_states.py`
- Modify: `tests/integration/test_orchestrator_persistence.py`

- [ ] **Step 1: Escribir los tests que fallan**

Primero, en **los dos** ficheros de integracion, dentro de
`_build_orchestrator`, agregar junto a `orch.x_error = None`:

```python
    orch.macro_error = None
```

Sin esto los tests mueren con `AttributeError`: los fixtures construyen el
orquestador con `__new__` y mockean `_fetch_weekly_close` entero, asi que
`_fetch_macro_snapshot` no corre nunca y `macro_error` no existiria.

Despues, agregar a `tests/integration/test_orchestrator_exit_states.py`:

```python
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
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v`
Expected: los tres primeros fallan porque no se manda ninguna alerta —
`send_alert.call_args` es `None`, asi que sale `TypeError: 'NoneType' object is
not subscriptable`, y el de orden sale por `StopIteration`. El cuarto
(`..._says_nothing`) **pasa ya**, porque hoy no hay alerta: es el que fija que
no aparezca una de mas.

- [ ] **Step 3: Implementar**

En `src/macro_pipeline/orchestration/main.py`, insertar **inmediatamente
despues** del bloque de la alerta de publicadores (el que termina con
`"El cierre se publica igual si lo aprobás. Verificar con "` /
`` "`python scripts/check_publishers.py`." `` y su parentesis de cierre) y
**antes** de la linea `                # ── FASE HITL ────...`, con 16 espacios
de indentacion:

```python
                # ── Degradación: el bloque macro no llegó ──────────────────────
                # Mismo lugar y mismo motivo que los dos avisos de arriba: quien
                # aprueba tiene que saber que ese cierre sale con menos.
                # `macro_error` está cargado sólo cuando el bloque macro se
                # rompió. FRED sin key no llega acá con motivo: ADR-001 lo
                # declara opcional, y un opcional sin configurar no participa —
                # no degrada, así que no hay nada que avisar.
                if self.macro_error:
                    logger.warning("macro_degraded", reason=self.macro_error)
                    self.telegram.send_alert(
                        "⚠️ El cierre semanal sale sin bloque macro.\n\n"
                        f"Motivo: {self.macro_error}\n\n"
                        "El cierre se publica igual si lo aprobás: los índices "
                        "son el contenido principal y el macro es contexto."
                    )
```

- [ ] **Step 4: Verificar, incluida la comprobacion por mutacion**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q` → todo verde.

**Mutacion obligatoria.** Reemplazar `f"Motivo: {self.macro_error}\n\n"` por un
texto fijo, `"Motivo: el bloque macro no se pudo construir.\n\n"`, y volver a
correr. **Tiene que fallar** `test_the_macro_warning_carries_the_validator_cause_verbatim`
y `test_a_broken_macro_block_still_publishes_and_warns`. Restaurar y confirmar
verde. Reportar los dos numeros: es la evidencia de que la causa esta fijada por
tests y no solo escrita.

Revisar tambien que `test_a_disabled_network_never_warns` (que usa
`assert_not_called()` a secas y por lo tanto se acopla a **toda** alerta de la
run) siga en verde: su fixture deja `macro_error` en None, asi que deberia. Si
fallara, es que el fixture cambio y hay que mirarlo, no relajar la asercion.

Run: `./.venv/Scripts/python.exe -m mypy src scripts` → `Success`, 30 ficheros.
Run: los dos comandos de `ruff` → limpio.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/
git commit -m "feat(orchestration): avisar cuando el bloque macro se cae por un fallo"
```

Con los dos trailers.

---

### Task 4: ADR-009 — el segundo eje, y la revision de los catorce casos

**Files:**
- Modify: `docs/adr/009-degradation-policy.md`

- [ ] **Step 1: Agregar el eje**

En la seccion **Decisión**, despues del bloque "El segundo eje: qué estado deja
un abort" y antes de "La política, por componente", agregar:

```markdown
### El tercer eje: lo declarado opcional no degrada

La formulación obvia —*un componente sin configurar no alerta*— **no sobrevive
al inventario**, y por eso queda escrita como descartada: para X y LinkedIn una
credencial faltante **sí** alerta. Con el eje puesto en "credencial presente"
eso sería una contradicción.

No lo es:

> **Un componente declarado opcional, cuando no está configurado, no participa
> — y no participar no es degradar.** Un componente necesario al que le faltan
> credenciales es un fallo.

"Declarado opcional" no es una opinión sobre cada componente: **ADR-007 lo dice
de R2 y ADR-001 lo dice del bloque macro**. Publicar, en cambio, es el propósito
del pipeline. El eje se apoya en declaraciones que ya existen, y por eso es
verificable en vez de retórico — de un componente nuevo se puede preguntar "¿lo
declara opcional algún ADR?" y la respuesta no depende de quién conteste.

De acá sale que **FRED sin key, R2 sin configurar y una red apagada por bandera
son la misma cosa**: no participan, y no gastan una alerta. Los tres fallando
*estando configurados* sí alertan.
```

- [ ] **Step 2: Partir la fila de FRED y corregir la de R2**

En la tabla "La política, por componente", reemplazar esta fila:

```
| FRED (bloque macro) | Sin key, API caída, serie corta, dato rancio o fuera de rango | **Degrada** — `macro=None` | — |
```

por estas dos:

```
| FRED (bloque macro) | Sin key | **No participa** — opcional sin configurar (ADR-001), sin alerta | — |
| FRED (bloque macro) | API caída, serie corta, dato rancio o cifra fuera de rango | **Degrada** — `macro=None`, con alerta que nombra la causa | — |
```

Y reemplazar esta:

```
| R2 | Sin configurar **o** subida fallida | **Degrada** — sin snapshot remoto, con aviso | — |
```

por estas dos:

```
| R2 | Sin configurar | **No participa** — opcional sin configurar (ADR-007), sin alerta | — |
| R2 | Subida fallida | **Degrada** — sin snapshot remoto, con aviso | — |
```

**La fila vieja de R2 afirmaba un aviso que no existe**: `orchestration/main.py`
entra al bloque de subida sólo `if self.r2_ready`, así que con R2 sin configurar
no se manda nada. Bajo el eje nuevo ese silencio es correcto y **no se toca el
código**: lo que estaba mal era la fila.

- [ ] **Step 3: Cerrar la limitación (c)**

Reemplazar el párrafo completo de `**(c) La regla "toda degradación alerta" no
se cumple para FRED.**` por:

```markdown
**(c) La regla "toda degradación alerta" no se cumplía para FRED.** — *Cerrada
el 2026-08-25.* El bloque macro se caía en silencio por tres caminos que
`_fetch_macro_snapshot` no distinguía. Dos de ellos —la API/serie/frescura y el
validador rechazando la cifra— son fallos y ahora alertan con la causa real; el
tercero, FRED sin key, no es un fallo sino un opcional sin configurar, y sigue
en silencio por el tercer eje de arriba.

Lo que la pregunta destapó fue más grande que FRED: la respuesta correcta ya se
cumplía en R2 y en los publicadores por decisiones locales que nadie había
escrito como política, y la fila de R2 de esta misma tabla afirmaba un aviso que
el código no manda. Es el mismo patrón que motivó este ADR.
```

- [ ] **Step 4: Revisar los catorce casos contra el eje nuevo**

Este paso es trabajo, no una formalidad: fue escribir el inventario lo que
destapó las tres divergencias la primera vez, y esta ronda ya destapó la de R2.

Recorrer **cada fila** de la tabla "La política, por componente" y, para cada
una, verificar contra el código a qué corresponde y anotar el `file:symbol`
donde se comprobó:

1. ¿El componente está declarado opcional por algún ADR? ¿Cuál?
2. Si lo está: ¿el camino de "sin configurar" es silencioso en el código?
3. ¿La columna de alerta de la fila coincide con lo que el código manda?

Reportar el resultado como una lista de catorce lineas, una por fila, cada una
con el `file:symbol` verificado y `OK` o la divergencia encontrada.

**Si aparece una divergencia nueva:** si es del mismo tipo que la de R2 —una
fila que miente sobre algo que el código ya hace bien— corregir la fila en este
mismo commit. Si es del otro tipo —el código no hace lo que la política dice—
**anotarla y NO arreglarla**: se decide por separado. Agregarla como una
limitación `(d)` con la misma forma que (a) y (b), describiendo el modo de fallo
y dejando explícito que queda sin decidir.

- [ ] **Step 5: Verificar y commitear**

Releer el ADR entero: que no quede ninguna frase contradiciendo el eje nuevo,
que los conteos de filas y de "casos" que el texto mencione sigan siendo
correctos, y que la prosa nueva concuerde con las tablas.

Run: `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q` —
un cambio solo de documentacion no debe mover nada, y eso es lo que se confirma.

```bash
git add docs/adr/009-degradation-policy.md
git commit -m "docs: ADR-009 con el eje de lo opcional y la limitacion (c) cerrada"
```

Con los dos trailers.

---

## Verificación final

- [ ] `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q` — verde, sin skips nuevos.
- [ ] `./.venv/Scripts/python.exe -m pytest tests/unit tests/integration --cov=src --cov-fail-under=60 -q` — la cobertura no baja de 82.95%.
- [ ] `./.venv/Scripts/python.exe -m mypy src scripts` — `Success: no issues found in 30 source files`.
- [ ] `./.venv/Scripts/python.exe -m ruff check .` y `-m ruff format --check .` — limpio.
- [ ] La mutación del Task 3 documentada con los dos números.
- [ ] La lista de catorce líneas del Task 4, con su `file:symbol` cada una.
- [ ] `git push` y **mirar el run de CI hasta el final**, no solo el verde local.
      Es el primer sitio donde este código corre fuera del `.venv` de esta
      máquina.
