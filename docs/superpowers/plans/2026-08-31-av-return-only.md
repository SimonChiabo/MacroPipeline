# Publicar sólo el retorno por la ruta de Alpha Vantage — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el fallback a Alpha Vantage de un abort en una degradación real: sin el nivel de cierre —que sería el del ETF bajo la etiqueta del índice— pero con el retorno, que es invariante de escala.

**Architecture:** `WeeklyCloseData.sp500_close` / `nasdaq_close` pasan a `float | None`, obligatorios y sin default. La rama de AV de `_fetch_weekly_close` los deja en `None`, y los cuatro consumidores lo tratan: el validador saltea los rangos de nivel, el renderer sube el retorno al lugar del cierre, la capa LLM no participa y `mark_as_published` escribe `NULL`. FMP sin key se muda del bloque de abortos al de degradaciones, con el aviso repartido como en FRED: el arranque avisa por la key faltante, la run avisa sólo por la caída en ejecución.

**Tech Stack:** Python 3.12, Pydantic v2 (`strict=True`), Playwright, SQLite, pytest, `ruff` (line-length 88), `mypy --strict`.

**Diseño:** `docs/superpowers/specs/2026-08-31-av-return-only-design.md`

**Intérprete:** siempre `./.venv/Scripts/python.exe -m ...`. Nunca `python` a secas.

---

## Estructura de ficheros

| Fichero | Responsabilidad | Tarea |
|---|---|---|
| `src/macro_pipeline/validators/schemas.py` | El tipo que hace representable «no hay nivel publicable» | 1 |
| `src/macro_pipeline/validators/engine.py` | Saltear el rango de nivel sólo cuando no hay nivel | 2 |
| `src/macro_pipeline/render/playwright_engine.py` | Armar las tarjetas de métrica en Python | 3 |
| `src/macro_pipeline/templates/weekly_close.html` | Recibir `{metrics_grid}` en vez de cuatro claves | 3 |
| `src/macro_pipeline/orchestration/main.py` | Producir el `None`, no llamar al LLM, alertar, mover la rama de FMP | 4, 5, 6, 7 |
| `docs/adr/009-degradation-policy.md` | La política escrita, fila por fila | 8 |

**Nada que tocar en `storage/state.py`:** `mark_as_published` ya declara los cuatro parámetros como `float | None` y las columnas son `REAL`. Verificado también el único lector, `get_publication_state`, que se consume por `x_post_id`/`linkedin_post_id` (`main.py:651-652`). **No hay migración en este plan.** Si en algún momento parece que hace falta una, es señal de que algo se entendió mal — parar y releer.

---

## Task 1: `WeeklyCloseData` admite el par de cierres en `None`

**Files:**
- Modify: `src/macro_pipeline/validators/schemas.py:31-48`
- Test: `tests/unit/test_validators.py`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `tests/unit/test_validators.py`:

```python
def test_weekly_close_acepta_el_par_de_cierres_en_none():
    """La ruta de AV no puede publicar el nivel: `None` es un valor legítimo."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=None,
        sp500_weekly_return=0.012,
        nasdaq_close=None,
        nasdaq_weekly_return=0.019,
    )
    assert data.sp500_close is None
    assert data.nasdaq_close is None


def test_weekly_close_rechaza_omitir_el_cierre():
    """Sin default: olvidarse del cierre es un error, no una degradación.

    Es la diferencia entre declarar «este cierre no se puede publicar» y
    que se caiga solo por descuido en algún sitio que nadie mira.
    """
    with pytest.raises(PydanticValidationError, match="sp500_close"):
        WeeklyCloseData(
            date=date(2026, 8, 21),
            sp500_weekly_return=0.012,
            nasdaq_close=16000.0,
            nasdaq_weekly_return=0.019,
        )


def test_weekly_close_rechaza_el_par_a_medias():
    """Las dos cifras salen del mismo cliente en la misma rama.

    Una poblada y la otra en `None` no sale de ninguna fuente real: si
    aparece es un bug, y tiene que reventar acá y no tres capas más abajo.
    """
    with pytest.raises(PydanticValidationError, match="mismo instrumento"):
        WeeklyCloseData(
            date=date(2026, 8, 21),
            sp500_close=7657.71,
            sp500_weekly_return=0.012,
            nasdaq_close=None,
            nasdaq_weekly_return=0.019,
        )


```

**No agregar un test del cierre negativo:** `test_pydantic_schema_strictness` (`tests/unit/test_validators.py:63`) ya lo cubre con `sp500_close=-100.0`. Lo que hay que hacer con él es **verificar que sigue pasando** después del Step 3 — es la prueba de que `gt=0` no se perdió al volver el campo opcional en el tipo. Duplicarlo no agrega cobertura, agrega un sitio más donde mentir.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -k "par_de_cierres or omitir or par_a_medias" -v`

Expected: FAIL. Los dos primeros con `ValidationError` por `sp500_close` (hoy no admite `None` ni se puede omitir); el tercero pasa por el motivo equivocado — hoy no existe el mensaje "mismo instrumento", así que falla el `match`.

- [ ] **Step 3: Implementar**

En `src/macro_pipeline/validators/schemas.py`, cambiar el import:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator
```

Reemplazar los dos campos de cierre y agregar el validador de modelo dentro de `WeeklyCloseData`:

```python
    sp500_close: float | None = Field(
        ...,
        gt=0,
        description=(
            "Precio de cierre del SP500, o None si la fuente no cotiza el "
            "índice (la ruta de Alpha Vantage devuelve el ETF)"
        ),
    )
    sp500_weekly_return: float = Field(
        ..., description="Retorno semanal del SP500 (ej. 0.05 para 5%)"
    )
    nasdaq_close: float | None = Field(
        ...,
        gt=0,
        description=(
            "Precio de cierre del NASDAQ, o None si la fuente no cotiza el "
            "índice"
        ),
    )
    nasdaq_weekly_return: float = Field(..., description="Retorno semanal del NASDAQ")
    macro: MacroSnapshot | None = Field(
        default=None,
        description=(
            "Contexto macro opcional: si FRED falla, el cierre se publica sin él"
        ),
    )

    @model_validator(mode="after")
    def _cierres_correlacionados(self) -> "WeeklyCloseData":
        """Los dos niveles vienen del mismo cliente, así que van juntos.

        Es lo que habilita a que todo aguas abajo pregunte una sola cosa
        —`sp500_close is None`— sin volver a razonar la correlación.
        """
        if (self.sp500_close is None) != (self.nasdaq_close is None):
            raise ValueError(
                "Los dos cierres vienen del mismo instrumento y de la misma "
                "llamada: o están los dos, o no está ninguno."
            )
        return self
```

**Nota sobre el `...`:** es obligatorio y no `default=None`. Con un default, omitir el campo devuelve `None` en silencio, que es una degradación por descuido — exactamente lo que este trabajo existe para no tener.

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -v`

Expected: PASS, todos. Los tests viejos del fichero no cambian: siguen pasando las dos cifras.

- [ ] **Step 5: Correr la suite entera — este cambio toca un tipo compartido**

Run: `./.venv/Scripts/python.exe -m pytest -q`

Expected: PASS, 286 tests (283 + 3). Si algo falla, es un sitio que construía `WeeklyCloseData` sin cierre y hasta ahora nadie lo notaba: arreglarlo antes de seguir.

- [ ] **Step 6: Lint y tipos**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add src/macro_pipeline/validators/schemas.py tests/unit/test_validators.py
git commit -m "feat(schemas): el nivel de cierre puede faltar, pero nunca a medias"
```

---

## Task 2: El validador saltea el rango de nivel sólo cuando no hay nivel

**Files:**
- Modify: `src/macro_pipeline/validators/engine.py:82-110`
- Test: `tests/unit/test_validators.py`

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_validate_weekly_close_pasa_sin_niveles(engine):
    """Sin nivel no hay rango que aplicar: es la ruta de AV publicando."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=None,
        sp500_weekly_return=0.012,
        nasdaq_close=None,
        nasdaq_weekly_return=0.019,
    )
    assert engine.validate_weekly_close(data) is True


def test_validate_weekly_close_sin_niveles_sigue_validando_retornos(engine):
    """En la ruta de AV los rangos de retorno son la única defensa numérica.

    Saltear el nivel no puede convertirse en saltear el control entero.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=None,
        sp500_weekly_return=0.80,  # +80% en una semana: imposible
        nasdaq_close=None,
        nasdaq_weekly_return=0.019,
    )
    with pytest.raises(ValidationError, match="Retorno del SP500"):
        engine.validate_weekly_close(data)
```

Usar la misma fixture `engine` que ya usan los tests del fichero.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -k "sin_niveles" -v`

Expected: FAIL con `TypeError: '<=' not supported between instances of 'int' and 'NoneType'` — hoy el bucle compara `None` contra el mínimo.

- [ ] **Step 3: Implementar**

En `src/macro_pipeline/validators/engine.py`, reemplazar el comentario y el bucle de niveles:

```python
        # Validar niveles de cierre. Un `close` correcto del instrumento
        # equivocado (el ETF en vez del indice) pasa todos los demas controles:
        # el retorno es invariante de escala y el esquema solo exige `gt=0`.
        #
        # `None` es distinto de un nivel malo: significa que la fuente no
        # cotiza el indice y el nivel no se va a publicar (ADR-009,
        # divergencia 4). No hay cifra que defender, asi que no hay rango que
        # aplicar. Un nivel *poblado* fuera de rango sigue abortando: eso no
        # se relaja.
        niveles = [
            (
                "SP500",
                data.sp500_close,
                rules.get("sp500_close_min", 0.0),
                rules.get("sp500_close_max", float("inf")),
            ),
            (
                "NASDAQ",
                data.nasdaq_close,
                rules.get("nasdaq_close_min", 0.0),
                rules.get("nasdaq_close_max", float("inf")),
            ),
        ]
        for label, value, minimo, maximo in niveles:
            if value is None:
                continue
            if not (minimo <= value <= maximo):
```

El cuerpo del `if` (el `logger.error` y el `raise`) queda igual.

- [ ] **Step 4: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -v`

Expected: PASS. **Verificar en particular que `test_validate_weekly_close_rejects_an_etf_scale_level` (`tests/unit/test_validators.py:98`, el `sp500_close=765.72`, el SPY real del 2026-08-21) sigue pasando** — no perdió cobertura, cambió de significado: ahora prueba que un 765 que se coló *poblado* sigue siendo un abort.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/validators/engine.py tests/unit/test_validators.py
git commit -m "feat(validators): sin nivel no hay rango de nivel que aplicar"
```

---

## Task 3: El renderer arma las tarjetas y omite el cierre ausente

**Files:**
- Modify: `src/macro_pipeline/render/playwright_engine.py:71-105`
- Modify: `src/macro_pipeline/templates/weekly_close.html:64-76` (CSS) y `:122-134` (markup)
- Test: `tests/unit/test_render_playwright.py`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/unit/test_render_playwright.py`:

```python
def _weekly_close_sin_niveles(macro=None):
    return WeeklyCloseData(
        date=date(2026, 8, 7),
        sp500_close=None,
        sp500_weekly_return=0.0369,
        nasdaq_close=None,
        nasdaq_weekly_return=-0.0498,
        macro=macro,
    )


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_sin_niveles_muestra_el_retorno_y_ningun_nivel(mock_sync_playwright):
    """Lo que no se puede rotular, no aparece: la imagen nunca miente."""
    html = _rendered_html(mock_sync_playwright, _weekly_close_sin_niveles())

    assert "+3.69%" in html
    assert "-4.98%" in html
    assert "variación semanal" in html
    # Ninguna de las dos tarjetas trae un nivel: no hay separador de miles
    # en el cuerpo de las métricas.
    assert "metric-return" not in html


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_con_niveles_no_cambia(mock_sync_playwright):
    """La ruta de FMP renderiza exactamente como antes."""
    html = _rendered_html(mock_sync_playwright, _weekly_close())

    assert "7,712.33" in html
    assert "26,372.33" in html
    assert "+3.69%" in html
    assert "variación semanal" not in html


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_sin_niveles_conserva_el_bloque_macro(mock_sync_playwright):
    """Las dos degradaciones son independientes y se combinan."""
    html = _rendered_html(mock_sync_playwright, _weekly_close_sin_niveles(_macro()))

    assert "IPC interanual" in html
    assert "variación semanal" in html
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_render_playwright.py -k "sin_niveles or no_cambia" -v`

Expected: FAIL con `PlaywrightEngineError` envolviendo un `TypeError: unsupported format string passed to NoneType.__format__` — es el fail-loud del diseño funcionando antes de que exista el arreglo.

- [ ] **Step 3: Implementar el renderer**

En `src/macro_pipeline/render/playwright_engine.py`, agregar los dos helpers justo después de `_build_macro_block`:

```python
    def _build_metric_card(
        self, title: str, close: float | None, weekly_return: float
    ) -> str:
        """Una tarjeta de métrica. Sin nivel, el retorno ocupa su lugar.

        Se arma en Python y no en la plantilla por el mismo motivo que
        `_build_macro_block`: es un bloque que a veces no está entero, y una
        plantilla con `.format()` no sabe omitir una fila.
        """
        clase = "positive" if weekly_return >= 0 else "negative"
        retorno = f"{weekly_return * 100:+.2f}%"

        if close is None:
            # La fuente no cotiza el índice (ADR-009, divergencia 4). El nivel
            # no se publica: sería el del ETF bajo la etiqueta del índice.
            cuerpo = (
                f'<div class="metric-value {clase}">{retorno}</div>'
                '<div class="metric-note">variación semanal</div>'
            )
        else:
            cuerpo = (
                f'<div class="metric-value">{close:,.2f}</div>'
                f'<div class="metric-return {clase}">{retorno}</div>'
            )

        return (
            '<div class="metric-card">'
            f'<div class="metric-title">{title}</div>'
            f"{cuerpo}"
            "</div>"
        )

    def _build_metrics_grid(self, data: WeeklyCloseData) -> str:
        """Las dos tarjetas de índices."""
        return (
            '<div class="metrics-grid">'
            + self._build_metric_card(
                "S&amp;P 500", data.sp500_close, data.sp500_weekly_return
            )
            + self._build_metric_card(
                "NASDAQ", data.nasdaq_close, data.nasdaq_weekly_return
            )
            + "</div>"
        )
```

Y reemplazar el bloque de `.format()` en `render_weekly_close` (borrando las dos líneas de `sp500_class`/`nasdaq_class`, que se mudaron al helper):

```python
        # Inyección simple de texto usando el formato estándar de Python
        try:
            html_content = template.format(
                date=data.date.strftime("%Y-%m-%d"),
                metrics_grid=self._build_metrics_grid(data),
                macro_block=self._build_macro_block(data.macro),
            )
```

- [ ] **Step 4: Implementar la plantilla**

En `src/macro_pipeline/templates/weekly_close.html`, reemplazar el bloque de markup:

```html
        {metrics_grid}
```

(borrando el `<div class="metrics-grid">…</div>` entero con sus dos `metric-card`).

Y agregar el estilo de la nota, justo después de la regla `.metric-return`. **Recordar que las llaves van dobladas** en este fichero, porque pasa por `.format()`:

```css
        .metric-note {{
            font-size: 20px;
            opacity: 0.5;
            margin-top: 4px;
        }}
```

- [ ] **Step 5: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_render_playwright.py -v`

Expected: PASS, todos — incluidos los que ya existían.

- [ ] **Step 6: Lint y tipos**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add src/macro_pipeline/render/playwright_engine.py src/macro_pipeline/templates/weekly_close.html tests/unit/test_render_playwright.py
git commit -m "feat(render): la tarjeta de metrica se arma en Python y omite el nivel ausente"
```

---

## Task 4: La rama de AV produce los cierres en `None`, y FMP ausente cae al fallback

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:283-320` y `:372-390`
- Modify: `src/macro_pipeline/orchestration/main.py` (`__init__`, junto a `self.macro_error`)
- Modify: los seis helpers de test que arman el orquestador a mano
- Test: `tests/unit/test_orchestrator_startup.py` — es donde ya viven los tests de `_fetch_weekly_close`, con la fixture `data_orch` (línea 39). **Reusarla; no crear una fixture paralela.**

- [ ] **Step 0: Reescribir el test que este trabajo invalida**

`tests/unit/test_orchestrator_startup.py:188` afirma hoy exactamente lo contrario de lo que va a pasar:

```python
def test_the_etl_refuses_to_run_without_fmp(data_orch):
    """No lo alcanza ningun camino: el punto de decision aborta antes."""
    data_orch.fmp = None

    with pytest.raises(RuntimeError, match="punto de decisión"):
        data_orch._fetch_weekly_close()
```

**Reemplazarlo** —no borrarlo— por su sucesor, que fija la conducta nueva:

```python
def test_the_etl_falls_back_to_av_without_fmp(data_orch):
    """FMP sin key dejo de abortar el dia que paso a degradar.

    La guarda sigue existiendo, pero ahora es como se *entra* al fallback y no
    como se mata la run. Este test es el que impide que alguien la devuelva a
    un `raise` fuera del `try` y deje la politica diciendo «degrada» con el
    codigo abortando.
    """
    data_orch.fmp = None
    data_orch.component_errors["fmp"] = "Se requiere FMP_API_KEY."
    data_orch.av.get_daily_prices.side_effect = lambda s, outputsize: _precios(765.0)

    data, source = data_orch._fetch_weekly_close()

    assert source == "av"
    assert data.sp500_close is None
```

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/unit/test_orchestrator_startup.py`, junto a los tests de AV que ya están ahí:

```python
def _precios(base: float) -> pd.DataFrame:
    """Diez ruedas subiendo 0.2% diario, a la escala que se le pida."""
    fechas = pd.date_range(end=pd.Timestamp("2026-08-21"), periods=10, freq="B")
    return pd.DataFrame(
        {"date": fechas, "close": [base * (1 + i * 0.002) for i in range(10)]}
    )


def test_la_ruta_de_fmp_conserva_el_nivel(data_orch):
    data_orch.fmp.get_historical_prices.side_effect = lambda s: _precios(7657.0)

    data, source = data_orch._fetch_weekly_close()

    assert source == "fmp"
    assert data.sp500_close is not None
    assert data.sp500_close > 2000


def test_la_ruta_de_av_no_publica_el_nivel(data_orch):
    """El nivel de SPY bajo la etiqueta del índice es la invariante de ADR-001."""
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av.get_daily_prices.side_effect = lambda s, outputsize: _precios(765.0)

    data, source = data_orch._fetch_weekly_close()

    assert source == "av"
    assert data.sp500_close is None
    assert data.nasdaq_close is None
    # El retorno sí sobrevive al cambio de instrumento: es el punto entero.
    assert data.sp500_weekly_return == pytest.approx(0.018, abs=0.002)


def test_fmp_caido_en_ejecucion_deja_motivo_para_la_alerta(data_orch):
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av.get_daily_prices.side_effect = lambda s, outputsize: _precios(765.0)

    data_orch._fetch_weekly_close()

    assert data_orch.fmp_runtime_error is not None
    assert "503" in data_orch.fmp_runtime_error


def test_fmp_sin_key_no_deja_motivo_in_run(data_orch):
    """El arranque ya avisó por la key faltante: repetirlo es contarlo dos veces."""
    data_orch.fmp = None
    data_orch.component_errors["fmp"] = "Se requiere FMP_API_KEY."
    data_orch.av.get_daily_prices.side_effect = lambda s, outputsize: _precios(765.0)

    data_orch._fetch_weekly_close()

    assert data_orch.fmp_runtime_error is None
```

Si el fichero no importa `pandas`, agregar `import pandas as pd` arriba.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_startup.py -v`

Expected: FAIL. `test_la_ruta_de_av_no_publica_el_nivel` falla porque hoy `data.sp500_close == 765.x`; `test_the_etl_falls_back_to_av_without_fmp` falla con el `RuntimeError` de la guarda actual; los dos de `fmp_runtime_error` fallan con `AttributeError`.

- [ ] **Step 3: Implementar — mover la guarda dentro del `try`**

En `_fetch_weekly_close`, **borrar** la guarda actual (que está fuera del `try`):

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

y reemplazar el arranque del `try` por:

```python
        data_source = "fmp"

        try:
            # `self.fmp is None` dejo de ser inalcanzable el dia que FMP paso a
            # degradar: hasta entonces la rama 4 del punto de decision abortaba
            # antes de llegar. Va *dentro* del `try` a proposito — levantar aca
            # es como se entra al fallback, no como se mata la run.
            if self.fmp is None:
                motivo = self.component_errors.get(
                    "fmp", f"apagado con {USE_FMP_VAR}=false"
                )
                raise RuntimeError(f"FMP no disponible: {motivo}")
            sp500_df = self.fmp.get_historical_prices("^GSPC")
            nasdaq_df = self.fmp.get_historical_prices("^IXIC")
        except Exception as e:
            logger.warning("fmp_failed_falling_back_to_av", error=str(e))
            data_source = "av"
            # El aviso in-run es solo para la caida en ejecucion. FMP sin key
            # ya lo aviso el punto de decision al arrancar, y avisar de nuevo
            # seria el mismo fallo contado dos veces — es la forma exacta que
            # ya usa `macro_error` con FRED.
            if self.fmp is not None:
                self.fmp_runtime_error = str(e)
            try:
```

El resto de la rama de AV (la guarda de `self.av is None`, las dos llamadas a `get_daily_prices`, y el `except` de mock) queda **exactamente igual**.

- [ ] **Step 4: Implementar — el `None` en la construcción del modelo**

Reemplazar la construcción de `WeeklyCloseData` al final del método:

```python
        # El nivel de cierre se publica solo si vino del instrumento que dice
        # la etiqueta. FMP pide `^GSPC`/`^IXIC` y lo cumple; AV pide
        # `SPY`/`QQQ`, que cotizan a otra escala, y publicar ese numero
        # rotulado "SP500" es la invariante de ADR-001 rota en el ETL.
        # El retorno sobrevive al cambio de instrumento; el nivel no.
        #
        # Mock queda con nivel a proposito: sus cifras son de escala indice y
        # ya viven detras de `ALLOW_MOCK_DATA=false`.
        publica_nivel = data_source != "av"

        data = WeeklyCloseData(
            date=(
                sp_last["date"].date()
                if isinstance(sp_last["date"], pd.Timestamp)
                else date.today()
            ),
            sp500_close=float(sp_last["close"]) if publica_nivel else None,
            sp500_weekly_return=float(sp_return),
            nasdaq_close=float(ndq_last["close"]) if publica_nivel else None,
            nasdaq_weekly_return=float(ndq_return),
            macro=self._fetch_macro_snapshot(),
        )
```

- [ ] **Step 5: Implementar — el atributo en `__init__`**

Junto a donde `__init__` inicializa `self.macro_error`, agregar:

```python
        # Cargado solo cuando FMP se cayo en ejecucion. Sin key no llega aca:
        # eso lo avisa el punto de decision. Mismo reparto que `macro_error`.
        self.fmp_runtime_error: str | None = None
```

- [ ] **Step 6: Actualizar los seis helpers de test que arman el orquestador a mano**

Estos usan `MacroOrchestrator.__new__`, así que no pasan por `__init__` y hay que darles el atributo. En cada uno, agregar `orch.fmp_runtime_error = None` justo debajo de la línea `orch.macro_error = None`:

- `tests/integration/test_orchestrator_exit_states.py:55`
- `tests/integration/test_orchestrator_persistence.py:53`
- `tests/integration/test_orchestrator_startup_gate.py:46`
- `tests/integration/test_orchestrator_state_sync.py:66`
- `tests/unit/test_orchestrator_macro.py:36`
- `tests/unit/test_orchestrator_startup.py:45`

- [ ] **Step 7: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator_startup.py -v && ./.venv/Scripts/python.exe -m pytest -q`

Expected: PASS en los dos.

- [ ] **Step 8: Lint y tipos**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: sin errores.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(etl): la ruta de AV no publica el nivel, y FMP ausente cae al fallback"
```

---

## Task 5: La capa LLM no participa cuando no hay nivel

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:678-697`
- Test: `tests/integration/test_orchestrator_exit_states.py` — **no** `tests/unit/test_orchestrator_llm.py`, que sólo cubre la construcción de la capa y no tiene orquestador de run completa. El helper a reusar es `_build_orchestrator(data, state)` (línea 45), que ya mockea `llm`, `validator_agent`, `telegram` y `_fetch_weekly_close`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/integration/test_orchestrator_exit_states.py`:

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k "sin_nivel or con_nivel" -v`

Expected: los dos de `sin_nivel` FAIL — hoy `generate_headline` se llama igual, y el `data_str` se construye con `f"{None:,.2f}"`, así que puede reventar con `TypeError` antes de llegar al assert. Las dos formas de fallar son correctas. `test_con_nivel_la_capa_llm_sigue_participando` pasa ya: es el que fija que la ruta de FMP no se toca.

- [ ] **Step 3: Implementar**

Reemplazar la condición de la rama y el log:

```python
                # La capa LLM tampoco participa sin nivel de cierre. No es una
                # politica sobre el LLM sino sobre el dato: sin nivel el
                # `data_str` no se construye, asi que la cifra mal rotulada no
                # llega a existir para el modelo y ADR-001 se sostiene por
                # construccion y no por una clausula del prompt.
                sin_nivel = data.sp500_close is None
                if self.llm is None or self.validator_agent is None or sin_nivel:
```

y dentro de esa rama, cambiar el log para que nombre la causa — es la cuarta forma de llegar al bloque genérico y las otras tres ya se distinguen:

```python
                    logger.info(
                        "llm_layer_not_participating",
                        event_id=event_id,
                        cause=(
                            "sin_nivel_publicable" if sin_nivel else "capa_no_disponible"
                        ),
                    )
```

El resto de la rama (`headline = _generic_headline(data)`, `validator_approved = None`, `prompt_version = None`) no cambia.

- [ ] **Step 4: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v`

Expected: PASS, todos — incluidos los que ya existían.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(llm): sin nivel de cierre la capa no participa y el data_str no se construye"
```

---

## Task 6: La alerta in-run de la ruta de AV

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:790-810` (justo antes del bloque de `macro_error`)
- Test: `tests/integration/test_orchestrator_exit_states.py`

- [ ] **Step 1: Escribir los tests que fallan**

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -k "ruta_av_avisa or dos_veces" -v`

Expected: el primero FAIL (no existe la alerta), el segundo PASS por vacuidad. **El segundo recién vale como test después del Step 3** — es el que impide que la alerta se mande siempre.

- [ ] **Step 3: Implementar**

Insertar justo **antes** del bloque `if self.macro_error:`:

```python
                # ── Degradación: el cierre sale por la ruta de Alpha Vantage ───
                # Mismo lugar y mismo motivo que el aviso del bloque macro de
                # abajo: quien aprueba tiene que saber que ese cierre sale con
                # menos. `fmp_runtime_error` esta cargado solo cuando FMP se
                # cayo en ejecucion; FMP sin key no llega aca porque lo aviso el
                # punto de decision, que es donde nace.
                if self.fmp_runtime_error:
                    logger.warning(
                        "data_source_degraded",
                        event_id=event_id,
                        reason=self.fmp_runtime_error,
                    )
                    telegram.send_alert(
                        "⚠️ FMP falló y el cierre sale por Alpha Vantage.\n\n"
                        f"Motivo: {self.fmp_runtime_error}\n\n"
                        "AV cotiza los ETF (`SPY`, `QQQ`) donde FMP cotiza los "
                        "índices (`^GSPC`, `^IXIC`), así que el nivel de cierre "
                        "no se publica: la imagen sale sólo con la variación "
                        "semanal, que sí es la misma cifra en los dos "
                        "instrumentos.\n\n"
                        "El titular sale del bloque genérico: la capa LLM no "
                        "participa cuando no hay nivel que redactar.\n\n"
                        "Se publica igual si lo aprobás."
                    )
```

- [ ] **Step 4: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_exit_states.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_exit_states.py
git commit -m "feat(alertas): la ruta de AV avisa antes de pedir aprobacion"
```

---

## Task 7: FMP sin key se muda de abortos a degradaciones

**Files:**
- Modify: `src/macro_pipeline/orchestration/main.py:63-70` (`_CONSECUENCIA`)
- Modify: `src/macro_pipeline/orchestration/main.py:495-511` (rama 4) y `:535-537` (filtro de la rama 5)
- Test: `tests/integration/test_orchestrator_startup_gate.py` — el helper es `_orchestrator(data, state)` (línea 39), que arma el punto de decisión entero con `telegram` mockeado. `x_ready` y `linkedin_ready` son propiedades derivadas del cliente, así que quedan en `True` solas.

**Peligro de esta tarea:** la rama 5 renderiza `_CONSECUENCIA[c]` con indexación directa, no con `.get()`. Sacar `"fmp"` del filtro sin agregar la clave hace que **la alerta de la degradación sea la excepción que mata la run**. Los dos cambios van juntos.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/integration/test_orchestrator_startup_gate.py`:

```python
def test_fmp_sin_key_degrada_en_vez_de_abortar(data, state):
    """La consecuencia que ADR-009 dejó escrita de antemano."""
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {"fmp": "Se requiere FMP_API_KEY."}

    assert orch._startup_exit_code(EVENT_ID) is None


def test_fmp_sin_key_alerta_y_nombra_la_consecuencia(data, state):
    """La rama 5 indexa `_CONSECUENCIA[c]` directo: sin la clave, KeyError.

    Este test es el que impide que el aviso de la degradación se convierta en
    la excepción que mata la run.
    """
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {"fmp": "Se requiere FMP_API_KEY."}

    orch._startup_exit_code(EVENT_ID)

    alerta = orch.telegram.send_alert.call_args[0][0]
    assert "fmp" in alerta
    assert "Alpha Vantage" in alerta


def test_use_fmp_false_sigue_siendo_pausa_en_silencio(data, state):
    """Un switch apagado es una decisión, no un fallo: no se sustituye."""
    orch = _orchestrator(data, state)
    orch.fmp = None
    orch.component_errors = {}

    assert orch._startup_exit_code(EVENT_ID) == 0
    orch.telegram.send_alert.assert_not_called()
```

Si el fichero no define `EVENT_ID`, usar `f"weekly_close_{date.today()}"` como en `test_orchestrator_exit_states.py:27`.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_startup_gate.py -k "fmp" -v`

Expected: los dos primeros FAIL (hoy devuelve `1` y alerta con el texto viejo); el tercero PASS ya (esa rama no cambia, y el test la fija para que no se rompa).

- [ ] **Step 3: Implementar — `_CONSECUENCIA`**

```python
_CONSECUENCIA = {
    "fmp": (
        "el cierre sale por Alpha Vantage, sólo con la variación semanal y "
        "sin el nivel de cierre"
    ),
    "av": "sin fallback si FMP falla, y el cierre no saldría",
    "fred": "el cierre sale sin bloque macro",
    "anthropic": "el cierre sale con el titular genérico",
    "r2": "sin copia remota de la imagen",
    "x": "el cierre sale solo en LinkedIn",
    "linkedin": "el cierre sale solo en X",
}
```

El texto de `"av"` cambia porque el viejo —*"y hoy esa ruta tampoco publicaría"*— pasa a ser falso con este trabajo.

- [ ] **Step 4: Implementar — la rama 4**

Reemplazar el bloque entero de FMP en `_startup_exit_code` por sólo la pausa:

```python
        # FMP sin key ya no aborta: degrada a la ruta de Alpha Vantage, que
        # publica el retorno sin el nivel (ADR-009, divergencia 4, cerrada).
        # Cae sola al bloque 5. Lo que queda aca es la pausa deliberada: un
        # switch apagado es una decision y no un fallo, asi que no se sustituye
        # por el fallback.
        if self.fmp is None and "fmp" not in self.component_errors:
            logger.info("pipeline_paused_fmp_disabled", event_id=event_id)
            return 0
```

- [ ] **Step 5: Implementar — el filtro de la rama 5**

```python
        degradaciones = {
            c: m for c, m in self.component_errors.items() if c != "telegram"
        }
```

- [ ] **Step 6: Correr los tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/integration/test_orchestrator_startup_gate.py -v && ./.venv/Scripts/python.exe -m pytest -q`

Expected: PASS en los dos.

- [ ] **Step 7: Lint y tipos**

Run: `./.venv/Scripts/python.exe -m ruff format --check . && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy --strict src`

Expected: sin errores.

- [ ] **Step 8: Commit**

```bash
git add src/macro_pipeline/orchestration/main.py tests/integration/test_orchestrator_startup_gate.py
git commit -m "feat(politica): FMP sin key degrada a la ruta de AV en vez de abortar"
```

---

## Task 8: ADR-009 — la tabla fila por fila y el cierre de la divergencia 4

**Files:**
- Modify: `docs/adr/009-degradation-policy.md:126-129` (tabla) y `:451-467` (divergencia 4)

- [ ] **Step 1: Actualizar las cuatro filas de la tabla**

```markdown
| FMP (índices) | Sin key | **Degrada** a Alpha Vantage — el cierre sale sin el nivel, con alerta desde el punto de decisión | — |
| FMP (índices) | Apagado con `USE_FMP=false` | **Aborta** en silencio — pausa deliberada; un switch apagado no se sustituye por el fallback | Ninguna fila |
| FMP (índices) | API caída | **Degrada** a Alpha Vantage — el cierre sale sin el nivel, con alerta in-run antes de pedir aprobación | — |
| Alpha Vantage (índices) | Sin key | **Degrada** — el fallback queda ausente, con alerta que dice que sin él un fallo de FMP deja la run sin cierre | — |
```

Y la fila del validador, que gana la excepción declarada:

```markdown
| `ValidationEngine` | Cifra **del cierre semanal** fuera de rango de plausibilidad | **Aborta** — es la última defensa de la invariante de ADR-001. Sin nivel (ruta de AV) no hay rango de nivel que aplicar; los de retorno siguen | `failed` |
```

- [ ] **Step 2: Cerrar la divergencia 4**

Reescribir el bloque de la divergencia 4 para que diga qué la cerró: que el nivel pasó a `float | None`, que la ruta de AV lo deja en `None` y por eso el validador no tiene rango que aplicar, que FMP sin key se mudó al bloque de degradaciones tal como esa misma divergencia lo dejó escrito de antemano, y que la capa LLM no participa sin nivel porque el `data_str` no se construye. Marcarla **cerrada el 2026-08-31** con el commit.

- [ ] **Step 3: Recorrer la tabla entera, fila por fila, contra el código**

**Obligatorio, no opcional.** Es lo que lleva seis hallazgos acumulados, uno de ellos un fallo introducido por el propio trabajo que lo encontró. Para cada fila de la tabla de `docs/adr/009-degradation-policy.md:121-152`, abrir el código que la implementa y verificar que la celda sigue siendo cierta después de estos cambios. Anotar cualquier divergencia nueva en la sección de divergencias en vez de arreglarla en silencio.

Prestar atención especial a:
- Las filas de Anthropic, ahora que hay una cuarta forma de llegar al bloque genérico.
- La fila de `Cálculo del retorno`, que es el abort que queda vivo en la ruta de AV.
- La fila de Mock Data, que sigue con nivel poblado a propósito.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/009-degradation-policy.md
git commit -m "docs(adr): la ruta de AV degrada, y la divergencia 4 queda cerrada"
```

---

## Task 9: Verificación por mutación

Es el estándar de la casa: si romper algo a propósito deja la suite verde, ese algo no está cubierto. **Cada mutación se revierte antes de la siguiente.**

- [ ] **Mutación 1: poblar el cierre en la rama de AV**

En `_fetch_weekly_close`, cambiar `publica_nivel = data_source != "av"` por `publica_nivel = True`.

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **FAIL**, al menos `test_la_ruta_de_av_no_publica_el_nivel`. Si pasa, falta un test. Revertir.

- [ ] **Mutación 2: devolver la guarda de FMP a un `raise` fuera del `try`**

Mover el `if self.fmp is None: raise ...` a antes del `try`.

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **FAIL**, al menos `test_the_etl_falls_back_to_av_without_fmp`. Revertir.

- [ ] **Mutación 3: dejar `"fmp"` en el filtro de la rama 5**

Volver a `if c not in ("fmp", "telegram")`.

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **FAIL**, al menos `test_fmp_sin_key_alerta_y_nombra_la_consecuencia`. Revertir.

- [ ] **Mutación 4: devolver el `data_str` con cierre a la capa LLM**

Quitar `or sin_nivel` de la condición de la rama.

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **FAIL**, al menos `test_sin_nivel_la_capa_llm_no_participa`. Revertir.

- [ ] **Mutación 5: saltear también el rango de retorno cuando no hay nivel**

En `validate_weekly_close`, poner un `return True` temprano cuando `data.sp500_close is None`.

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **FAIL**, al menos `test_validate_weekly_close_sin_niveles_sigue_validando_retornos`. Revertir.

- [ ] **Verificar que el árbol quedó limpio tras revertir todo**

Run: `git status --porcelain`
Expected: sin salida.

---

## Task 10: Cierre

- [ ] **Step 1: Suite completa, lint y tipos sobre el HEAD final**

```bash
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m mypy --strict src
```

Expected: todo verde. El conteo esperado ronda los 305 tests (283 de base + ~22 nuevos); lo que importa es que no bajó de 283 y que ninguno quedó saltado.

- [ ] **Step 2: Empujar y esperar CI verde sobre el HEAD exacto**

```bash
git push
gh run list --limit 3
```

Anotar el número de run. **La barra es CI verde sobre el HEAD exacto, con el número anotado** — no "CI verde en algún commit de la serie".

- [ ] **Step 3: Cerrar el punto 6 en memoria**

Actualizar `macropipeline-pending-work.md` con la misma disciplina que los puntos 7 a 13: qué lo cerró, los commits, el número de run de CI, y **lo que costó verificar y no conviene volver a descubrir**. Como mínimo:

- La guarda de `self.fmp is None` existía y era código muerto declarado; el cambio de política la volvió camino de ejecución, y dejarla como estaba habría hecho que el trabajo entero no hiciera nada.
- `_CONSECUENCIA` no tenía clave `"fmp"` y la rama 5 la indexa directo: mover la rama sin agregar la clave convierte la alerta de la degradación en la excepción que mata la run.
- En la ruta de AV, los rangos de retorno quedan como única defensa numérica.
- El campo va con `...` y no `default=None` a propósito: con default, olvidarse del cierre degrada en silencio.

Actualizar también el `description` del fichero de memoria y la línea de `MEMORY.md`. Con el punto 6 cerrado, el backlog numerado queda sin puntos abiertos: decir eso explícitamente en vez de dejarlo deducir.

---

## Auto-revisión del plan

**Cobertura de la spec:**

| Sección de la spec | Tarea |
|---|---|
| §1 Dónde nace el `None` (tipo, `...`, `model_validator`) | 1 |
| §1 La guarda de FMP cambia de significado | 4 |
| §2 Validador | 2 |
| §2 Renderer y plantilla | 3 |
| §2 Capa LLM | 5 |
| §2 Persistencia (sin cambios, verificado) | — (nota en Estructura de ficheros) |
| §3 Rama 5 + `_CONSECUENCIA["fmp"]` + filtro | 7 |
| §3 Alerta in-run | 6 |
| §3 Textos que quedan mintiendo | 7 (los dos de código), 8 (el de la tabla) |
| §3 `USE_FMP=false` no se toca | 7, con test que lo fija |
| §4 ADR-009 y recorrido fila por fila | 8 |
| §5 Pruebas y verificación por mutación | 1-7 (tests), 9 (mutación), 10 (cierre) |

Sin huecos.

**Consistencia de nombres entre tareas:** `fmp_runtime_error` (4, 6), `publica_nivel` (4, 9), `sin_nivel` (5, 9), `_build_metric_card` / `_build_metrics_grid` (3), `{metrics_grid}` (3), `_CONSECUENCIA["fmp"]` (7, 9). Coherentes.

**Dependencias de orden:** 1 antes que 2, 3, 4 y 5 (todas dependen del tipo). 4 antes que 6 (la alerta consume el atributo). 7 puede ir en cualquier momento después de 4, pero antes de 8. 9 y 10 al final.
