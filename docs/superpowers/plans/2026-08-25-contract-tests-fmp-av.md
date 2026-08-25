# Contract tests de FMP y Alpha Vantage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el nightly de ADR-008 detecte un cambio de schema, de unidad o de instrumento en FMP y Alpha Vantage antes de que rompa un viernes, y que el validador impida publicar un nivel que viene del instrumento equivocado.

**Architecture:** tres piezas independientes. Los rangos de nivel en `rules.yaml` (patron declarativo que el engine ya usa para los retornos) cierran el agujero que ningun contract test puede cerrar. Los contract tests siguen el patron de FRED: un fichero por API, `pytestmark = pytest.mark.contract`, credenciales via `require_api_key`. El workflow distingue el rate limit de Alpha Vantage de un cambio de contrato real.

**Tech Stack:** Python 3.12, pytest, pandas, ruff, mypy. Local: `.venv/Scripts/python.exe` (Windows). CI: `.github/workflows/contract-tests.yml`.

**Spec:** `docs/superpowers/specs/2026-08-25-contract-tests-fmp-av-design.md`

---

## Estructura de ficheros

| Fichero | Que cambia | Responsabilidad |
|---|---|---|
| `src/macro_pipeline/validators/rules.yaml` | 4 claves nuevas en `weekly_close` | los umbrales, declarativos |
| `src/macro_pipeline/validators/engine.py` | 2 chequeos en `validate_weekly_close` | aplicar los umbrales |
| `tests/unit/test_validators.py` | fixture + 3 casos | fijar la regla nueva |
| `tests/contract/conftest.py` | 2 fixtures | resolver credenciales |
| `tests/contract/test_fmp_contract.py` | nuevo | contrato de FMP |
| `tests/contract/test_av_contract.py` | nuevo | contrato de AV, con 1 sola llamada |
| `.github/workflows/contract-tests.yml` | pre-chequeo + salida a fichero + alerta | que el nightly no mienta ni confunda |

**El orden entre tareas importa:** la Task 5 (cargar los secrets) va **antes** de la
Task 6 (exigirlos en el workflow). Al reves, el nightly queda en rojo.

---

### Task 1: rangos de nivel de cierre en el validador

**Files:**
- Modify: `src/macro_pipeline/validators/rules.yaml`
- Modify: `src/macro_pipeline/validators/engine.py`
- Test: `tests/unit/test_validators.py`

Contexto para quien implemente: el pipeline pide a FMP `^GSPC` (el indice, hoy
~7.657) y si FMP falla cae a Alpha Vantage con `SPY` (el ETF, hoy ~765). Guarda
el nivel venga de donde venga (`main.py:183`) y lo publica rotulado
`"SP500: Cierre"` (`playwright_engine.py:94`). Hoy nada lo impide: el unico
control es `gt=0` en el esquema Pydantic.

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/unit/test_validators.py`, agregar las cuatro claves nuevas al YAML de
la fixture `engine` (que arma su propio fichero de reglas y por eso no ve las
de produccion). Dentro del bloque `weekly_close:`, despues de
`nasdaq_return_max: 0.30`, agregar:

```yaml
  sp500_close_min: 2000
  sp500_close_max: 30000
  nasdaq_close_min: 5000
  nasdaq_close_max: 100000
```

Y agregar estos tres tests despues de `test_validate_weekly_close_anomaly`:

```python
def test_validate_weekly_close_rejects_an_etf_scale_level(engine):
    """El caso que motivo la regla: SPY publicado como cierre del S&P 500.

    El pipeline pide a FMP `^GSPC` y cae a Alpha Vantage con `SPY`. Los dos
    devuelven un `close` correcto, pero a escalas distintas: 765,72 es el ETF
    y 7.657,71 el indice, el mismo dia. El nivel se guarda venga de donde
    venga y se publica rotulado "SP500: Cierre", asi que por la ruta de
    fallback se publicaria el numero del instrumento equivocado. Ninguna de
    las dos APIs esta incumpliendo nada: por eso el control vive aca y no en
    un contract test.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=765.72,  # SPY real del 2026-08-21
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=0.019,
    )
    with pytest.raises(ValidationError, match="Cierre de SP500"):
        engine.validate_weekly_close(data)


def test_validate_weekly_close_rejects_an_etf_scale_nasdaq(engine):
    """Lo mismo para el NASDAQ: QQQ ronda los 600, el indice los 26.000."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=7657.71,
        sp500_weekly_return=0.012,
        nasdaq_close=612.40,  # QQQ, no ^IXIC
        nasdaq_weekly_return=0.019,
    )
    with pytest.raises(ValidationError, match="Cierre de NASDAQ"):
        engine.validate_weekly_close(data)


def test_validate_weekly_close_accepts_index_scale_levels(engine):
    """Los niveles reales de los indices pasan: el rango es ancho a proposito."""
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=7657.71,
        sp500_weekly_return=0.012,
        nasdaq_close=26029.15,
        nasdaq_weekly_return=0.019,
    )
    assert engine.validate_weekly_close(data) is True
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -q
```

Esperado: los dos tests de rechazo FALLAN con `DID NOT RAISE ValidationError`.
`test_validate_weekly_close_accepts_index_scale_levels` PASA desde el principio
(no hay control que lo rechace); eso es correcto y no significa que sobre: es la
red que impide que el arreglo del Step 3 se pase de estricto.

- [ ] **Step 3: Agregar los umbrales a `rules.yaml`**

En `src/macro_pipeline/validators/rules.yaml`, dentro de `weekly_close:`,
despues de `nasdaq_return_max: 0.30`:

```yaml

  # Niveles de cierre. El pipeline pide a FMP `^GSPC`/`^IXIC` (indices) y cae a
  # Alpha Vantage con `SPY`/`QQQ` (ETFs, ~10x mas chicos), pero guarda el nivel
  # venga de donde venga y lo publica rotulado "SP500: Cierre". Estos rangos son
  # lo unico que impide publicar 765,72 como cierre del S&P 500.
  # Anchos a proposito: detectan un cambio de instrumento o de unidad, no la
  # fluctuacion normal del mercado.
  sp500_close_min: 2000
  sp500_close_max: 30000
  nasdaq_close_min: 5000
  nasdaq_close_max: 100000
```

- [ ] **Step 4: Aplicar los umbrales en `engine.py`**

En `validate_weekly_close`, despues del bloque que valida el retorno del NASDAQ
y **antes** de `logger.info("weekly_close_validated", ...)`, insertar:

```python
        # Validar niveles de cierre. Un `close` correcto del instrumento
        # equivocado (el ETF en vez del indice) pasa todos los demas controles:
        # el retorno es invariante de escala y el esquema solo exige `gt=0`.
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
            if not (minimo <= value <= maximo):
                logger.error(
                    "validation_failed",
                    reason="close_level_out_of_bounds",
                    indicator=label,
                    value=value,
                )
                raise ValidationError(
                    f"Cierre de {label} {value} fuera del rango permitido "
                    f"[{minimo}, {maximo}]: puede venir de otro instrumento."
                )
```

Los defaults son permisivos a proposito (`0.0` e `inf`): `rules.yaml` es la
fuente de verdad, y un fichero de reglas que no declara el umbral no debe
empezar a rechazar datos por su cuenta. Es lo mismo que hacen los retornos.

- [ ] **Step 5: Correr los tests y verificar que pasan**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_validators.py -q
```

Esperado: todos pasan.

- [ ] **Step 6: Correr la suite entera**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q
```

Esperado: todos en verde. Ojo con `tests/integration/`: sus `WeeklyCloseData`
usan `sp500_close=5100.0` y `nasdaq_close=16000.0`, que caen dentro de los
rangos nuevos. Si alguno falla por esto, **no** ensanchar el rango: ajustar el
dato del test, que es un valor de relleno.

- [ ] **Step 7: Gates y commit**

```bash
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
.venv/Scripts/python.exe -m mypy src/ scripts/
git add src/macro_pipeline/validators/ tests/unit/test_validators.py
git commit -m "feat(validators): rechazar niveles de cierre de otro instrumento"
```

---

### Task 2: fixtures de FMP y Alpha Vantage en el conftest

**Files:**
- Modify: `tests/contract/conftest.py`

- [ ] **Step 1: Agregar las dos fixtures**

Al final de `tests/contract/conftest.py`, con el mismo patron que `fred_client`:

```python
@pytest.fixture(scope="session")
def fmp_client():
    """Cliente FMP real, apuntando a la API de producción."""
    from macro_pipeline.data.fmp_client import FMPClient

    return FMPClient(api_key=require_api_key("FMP_API_KEY"))


@pytest.fixture(scope="session")
def av_client():
    """Cliente Alpha Vantage real.

    `scope="session"` no es cosmético acá: la capa gratuita tiene un throttle
    por minuto y cada llamada cuesta cuota, así que los tests comparten una
    sola respuesta (ver la fixture `spy_daily` en `test_av_contract.py`).
    """
    from macro_pipeline.data.av_client import AlphaVantageClient

    return AlphaVantageClient(api_key=require_api_key("ALPHA_VANTAGE_API_KEY"))
```

- [ ] **Step 2: Verificar que el conftest sigue importandose**

```bash
.venv/Scripts/python.exe -m pytest tests/contract/ -m contract --collect-only -q
```

Esperado: colecciona los tests de FRED y LLM sin error. Todavia no hay tests de
FMP ni AV: eso es lo correcto en este paso.

- [ ] **Step 3: Commit**

```bash
git add tests/contract/conftest.py
git commit -m "test(contract): fixtures de FMP y Alpha Vantage"
```

---

### Task 3: contract test de FMP

**Files:**
- Create: `tests/contract/test_fmp_contract.py`

- [ ] **Step 1: Escribir el fichero completo**

```python
"""Contract tests de la API de Financial Modeling Prep (ADR-008).

Verifican que lo que devuelve FMP sigue siendo lo que el pipeline espera. No
validan lógica propia —de eso se encargan los unit tests— sino que la forma y
la escala de la respuesta externa no cambiaron bajo nuestros pies.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from macro_pipeline.data.fmp_client import FMPClientError

pytestmark = pytest.mark.contract

# Los dos símbolos que consume el cierre semanal (`orchestration/main.py`).
SP500 = "^GSPC"
NASDAQ = "^IXIC"


@pytest.mark.parametrize(
    ("symbol", "low", "high"),
    [
        # Rangos deliberadamente anchos: detectan que FMP empezó a devolver
        # otro instrumento o cambió de unidad, no la fluctuación del mercado.
        # Referencia del 2026-08-24: ^GSPC 7.657,71 y ^IXIC 26.029,15.
        (SP500, 2000.0, 30000.0),
        (NASDAQ, 5000.0, 100000.0),
    ],
)
def test_historical_prices_keep_their_shape_and_scale(fmp_client, symbol, low, high):
    """Columnas, tipos, profundidad y escala del histórico diario."""
    df = fmp_client.get_historical_prices(symbol)

    assert len(df) > 0, f"FMP no devolvió observaciones para {symbol}."
    assert "date" in df.columns and "close" in df.columns
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_numeric_dtype(df["close"])

    # `main.py` exige seis filas para calcular el retorno de cinco días
    # hábiles y aborta la run con ValueError si no las tiene. Es el contrato
    # que de verdad importa, el equivalente del test de lookback de FRED.
    assert len(df) >= 6, (
        f"{symbol} devolvió {len(df)} filas; el cierre semanal necesita 6."
    )

    ultimo = df.sort_values("date").iloc[-1]
    assert low <= ultimo["close"] <= high, (
        f"{symbol} cerró en {ultimo['close']}, fuera de [{low}, {high}]: "
        "puede haber cambiado de instrumento o de unidad."
    )
    # El hueco máximo entre sesiones son cuatro días naturales (viernes a
    # martes con el lunes feriado). Cinco deja margen sin volverse inútil.
    assert ultimo["date"].date() >= date.today() - timedelta(days=5), (
        f"El último dato de {symbol} es del {ultimo['date'].date()}: "
        "la serie dejó de actualizarse."
    )


def test_unknown_symbol_raises_client_error(fmp_client):
    """Un símbolo inexistente sigue siendo un error, no un DataFrame vacío.

    El cliente levanta cuando la respuesta no es una lista con datos, porque
    FMP sirve payloads de error con HTTP 200. Si esto empieza a fallar, mirar
    qué devuelve FMP ahora antes de aflojar el assert: un DataFrame vacío que
    llegue hasta `main.py` se convierte en un ValueError mucho más lejos del
    origen.
    """
    with pytest.raises(FMPClientError):
        fmp_client.get_historical_prices("NO_EXISTE_ESTE_SIMBOLO_XYZ")
```

- [ ] **Step 2: Correrlo contra la API real**

```bash
.venv/Scripts/python.exe -m pytest tests/contract/test_fmp_contract.py -m contract -v
```

Esperado: 3 tests PASSED (dos del parametrize, uno del error).

Si `test_unknown_symbol_raises_client_error` falla, **no aflojar el assert**:
reportar que devuelve FMP hoy para un simbolo inexistente. Que un simbolo malo
produzca un DataFrame vacio en vez de una excepcion es exactamente el tipo de
cambio que este test existe para detectar.

- [ ] **Step 3: Gates y commit**

```bash
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
git add tests/contract/test_fmp_contract.py
git commit -m "test(contract): verificar el contrato de FMP contra la API real"
```

---

### Task 4: contract test de Alpha Vantage

**Files:**
- Create: `tests/contract/test_av_contract.py`

La restriccion que manda acá es la cuota: **una sola llamada a AV por corrida**,
compartida por todos los asserts mediante una fixture de sesion.

- [ ] **Step 1: Escribir el fichero completo**

```python
"""Contract tests de la API de Alpha Vantage (ADR-008).

Alpha Vantage es la fuente de respaldo: solo se usa cuando FMP falla. Eso la
hace más fácil de romper sin que nadie se entere, no menos importante.

Dos particularidades marcan estos tests. La primera es la cuota: la capa
gratuita tiene un throttle por minuto, así que hay **una sola llamada** por
corrida y todos los asserts comparten esa respuesta. La segunda es que el
cliente devuelve un DataFrame vacío —con un simple warning— cuando la
respuesta no trae `Time Series (Daily)`, así que un test que solo mirara los
nombres de columna pasaría con Alpha Vantage devolviendo cualquier cosa.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from macro_pipeline.data.av_client import AlphaVantageClientError

pytestmark = pytest.mark.contract

# El símbolo que el pipeline pide cuando FMP falla (`orchestration/main.py`).
SPY = "SPY"

# Marcador que el workflow busca en la salida de pytest para distinguir "no
# pudimos verificar el contrato" de "el contrato cambió". Las dos cosas ponen
# el nightly en rojo, pero solo una pide tocar código.
AV_RATE_LIMIT_MARKER = "AV_RATE_LIMIT"


@pytest.fixture(scope="session")
def spy_daily(av_client):
    """La única llamada a Alpha Vantage de toda la corrida.

    Un rate limit no es un cambio de contrato, pero tampoco es un test que
    pasó: el contrato quedó sin verificar. Falla —un nightly verde que no
    verificó nada es peor que uno rojo— nombrando la causa para que la alerta
    diga si hay que investigar o basta con relanzar.
    """
    try:
        return av_client.get_daily_prices(SPY, outputsize="compact")
    except AlphaVantageClientError as e:
        if "rate limit" in str(e).lower():
            pytest.fail(
                f"{AV_RATE_LIMIT_MARKER}: Alpha Vantage respondió rate limit, "
                f"así que el contrato no se pudo verificar. No es un cambio "
                f"de schema: relanzar el workflow. Detalle: {e}"
            )
        raise


def test_daily_prices_are_not_silently_empty(spy_daily):
    """El cliente devuelve un DataFrame vacío si falta `Time Series (Daily)`.

    Solo deja un `logger.warning` y sigue, así que sin este assert el resto
    del fichero pasaría con Alpha Vantage devolviendo un payload sin datos.
    Es la misma lección que dejó el contract test de FRED.
    """
    assert len(spy_daily) > 0, (
        "Alpha Vantage devolvió un DataFrame vacío: la respuesta no traía "
        "'Time Series (Daily)'."
    )
    # `main.py` necesita seis filas para el retorno de cinco días hábiles.
    assert len(spy_daily) >= 6


def test_numeric_columns_survive_the_string_conversion(spy_daily):
    """Alpha Vantage devuelve strings y el cliente los convierte.

    Lo hace con `pd.to_numeric(errors="coerce")`, así que un cambio de formato
    —separador de miles, moneda, un sufijo— no da error: produce NaN. El
    pipeline los arrastraría hasta el cálculo del retorno. Por eso se mira
    `notna()` y no solo el dtype.
    """
    for col in ("open", "high", "low", "close", "volume"):
        assert col in spy_daily.columns, f"Falta la columna {col}."
        assert pd.api.types.is_numeric_dtype(spy_daily[col])
        assert spy_daily[col].notna().all(), (
            f"La columna {col} tiene NaN: `to_numeric` no pudo convertir algún "
            "valor, así que Alpha Vantage cambió el formato."
        )


def test_prices_stay_in_plausible_scale_and_are_fresh(spy_daily):
    """Escala y frescura del ETF.

    El margen de frescura es ancho a propósito: el 2026-08-25 Alpha Vantage
    seguía dando el cierre del 21 mientras FMP ya tenía el del 24, o sea que
    va una sesión atrasada. Un umbral corto marcaría en rojo un dato correcto.
    """
    ultimo = spy_daily.sort_values("date").iloc[-1]

    # Referencia del 2026-08-21: SPY cerró en 765,72. Rango ancho: detecta un
    # cambio de instrumento o un split mal aplicado, no la fluctuación normal.
    assert 100.0 <= ultimo["close"] <= 3000.0, (
        f"SPY cerró en {ultimo['close']}, fuera de [100, 3000]: puede haber "
        "cambiado de instrumento o de unidad."
    )
    assert ultimo["date"].date() >= date.today() - timedelta(days=7), (
        f"El último dato de SPY es del {ultimo['date'].date()}: la serie "
        "dejó de actualizarse."
    )
```

- [ ] **Step 2: Correrlo contra la API real**

```bash
.venv/Scripts/python.exe -m pytest tests/contract/test_av_contract.py -m contract -v
```

Esperado: 3 tests PASSED con **una sola** llamada a Alpha Vantage.

Si aparece el rate limit, los tres salen como fallo con `AV_RATE_LIMIT` en el
mensaje: esperar un minuto y repetir. Ese resultado tambien sirve de evidencia
para el Step 3, asi que anotarlo si ocurre.

- [ ] **Step 3: Verificar el marcador sin depender de que AV lo devuelva**

El camino del rate limit es el que menos se ejercita y el que mas importa que
funcione. Forzarlo con un cliente falso, en un fichero temporal fuera del repo:

```python
from unittest.mock import MagicMock

import pytest

from macro_pipeline.data.av_client import AlphaVantageClientError


def test_marker(monkeypatch):
    cliente = MagicMock()
    cliente.get_daily_prices.side_effect = AlphaVantageClientError(
        "Rate limit excedido en Alpha Vantage (máximo llamadas/min o /día)."
    )
    from tests.contract import test_av_contract as mod

    # `pytest.raises(Exception)` NO sirve acá: `pytest.fail()` levanta
    # `Failed`, que hereda de `BaseException` y no de `Exception`, asi que no
    # lo captura. `pytest.fail.Exception` es la forma publica de referirse a
    # esa clase. (Verificado en pytest 9.0.3 durante la implementacion.)
    with pytest.raises(pytest.fail.Exception) as exc:
        mod.spy_daily.__wrapped__(cliente)
    assert "AV_RATE_LIMIT" in str(exc.value)
```

Correrlo con `pytest <fichero> -q`. Esperado: PASSED, o sea que el marcador
aparece en el mensaje del fallo. Borrar el fichero temporal después: no se
commitea. Si `__wrapped__` no funciona con la fixture, verificar el marcador
llamando directamente a la función interna o dejando constancia del mensaje de
fallo real del Step 2; lo que no vale es dar el camino por bueno sin verlo.

- [ ] **Step 4: Gates y commit**

```bash
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
git add tests/contract/test_av_contract.py
git commit -m "test(contract): verificar el contrato de Alpha Vantage con una sola llamada"
```

---

### Task 5: cargar los dos secrets

**Files:** ninguno del repo.

`gh secret list` hoy devuelve cuatro: `ANTHROPIC_API_KEY`, `FRED_API_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. El workflow ya pasa `FMP_API_KEY` y
`ALPHA_VANTAGE_API_KEY` como variables de entorno, pero llegan vacias.

**Esta tarea va antes de la Task 6.** Al reves, el pre-chequeo pone el nightly
en rojo por secrets que todavia no existen.

- [ ] **Step 1: Cargar las dos keys desde el `.env` local**

`gh secret set NOMBRE` a secas abre un prompt interactivo y se cuelga en un
contexto no interactivo: hay que pipear el valor por stdin. Sin imprimir los
valores:

```bash
grep '^FMP_API_KEY=' .env | cut -d= -f2- | tr -d '\r\n' | gh secret set FMP_API_KEY
grep '^ALPHA_VANTAGE_API_KEY=' .env | cut -d= -f2- | tr -d '\r\n' | gh secret set ALPHA_VANTAGE_API_KEY
```

- [ ] **Step 2: Verificar que quedaron cargados**

```bash
gh secret list
```

Esperado: seis entradas, incluidas `FMP_API_KEY` y `ALPHA_VANTAGE_API_KEY`.
`gh secret list` no muestra valores, solo nombres y fechas.

---

### Task 6: workflow — exigir los secrets y distinguir el rate limit

**Files:**
- Modify: `.github/workflows/contract-tests.yml`

- [ ] **Step 1: Agregar las dos keys al pre-chequeo**

En el paso `Comprobar secrets requeridos`, reemplazar el comentario y las dos
lineas de `missing`:

```sh
          # Obligatorias las dos APIs que hoy tienen contract tests: FRED y
          # Anthropic. Cuando lleguen los de FMP y Alpha Vantage, sus keys se
          # agregan aca (mientras tanto, `conftest.py` igual falla ruidosamente
          # en CI si falta una credencial que un test si pide).
          missing=""
          [ -n "${FRED_API_KEY}" ] || missing="${missing} FRED_API_KEY"
          [ -n "${ANTHROPIC_API_KEY}" ] || missing="${missing} ANTHROPIC_API_KEY"
```

por:

```sh
          # Las cuatro APIs que hoy tienen contract tests. `conftest.py` igual
          # falla ruidosamente en CI si falta una credencial que un test pide,
          # pero este paso corre antes de instalar nada y las nombra todas de
          # una en vez de morir en la primera.
          missing=""
          [ -n "${FRED_API_KEY}" ] || missing="${missing} FRED_API_KEY"
          [ -n "${ANTHROPIC_API_KEY}" ] || missing="${missing} ANTHROPIC_API_KEY"
          [ -n "${FMP_API_KEY}" ] || missing="${missing} FMP_API_KEY"
          [ -n "${ALPHA_VANTAGE_API_KEY}" ] || missing="${missing} ALPHA_VANTAGE_API_KEY"
```

Y en el bloque `env:` de **ese mismo paso**, agregar las dos que faltan (hoy
solo declara cuatro):

```yaml
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
          ALPHA_VANTAGE_API_KEY: ${{ secrets.ALPHA_VANTAGE_API_KEY }}
```

- [ ] **Step 2: Guardar la salida de pytest**

Reemplazar el `run:` del paso `Run contract tests`:

```yaml
        run: pytest tests/contract/ -v --timeout=30 -m contract
```

por:

```yaml
        run: |
          # `pipefail` o el `tee` se traga el codigo de salida de pytest y el
          # job sale en verde con los tests fallando.
          set -o pipefail
          pytest tests/contract/ -v --timeout=30 -m contract | tee pytest-output.txt
```

- [ ] **Step 3: Elegir el texto de la alerta segun el marcador**

En el paso `Notify on failure`, reemplazar la construccion del mensaje.
Actualmente es:

```sh
          run_url="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          status=$(curl -sS -o /dev/null -w '%{http_code}' \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="⚠️ Contract Tests fallaron. Revisar cambios de API. Workflow: ${run_url}")
```

Pasa a:

```sh
          run_url="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"

          # Un rate limit de Alpha Vantage y un cambio de schema ponen los dos
          # el nightly en rojo, pero solo uno pide tocar codigo. La alerta lo
          # dice para no tener que entrar al run para averiguarlo.
          if grep -q "AV_RATE_LIMIT" pytest-output.txt 2>/dev/null; then
            texto="⚠️ Contract tests en rojo: Alpha Vantage respondió rate limit, así que su contrato quedó sin verificar. NO es un cambio de API: basta con relanzar. ${run_url}"
          else
            texto="⚠️ Contract Tests fallaron. Revisar cambios de API. Workflow: ${run_url}"
          fi

          status=$(curl -sS -o /dev/null -w '%{http_code}' \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="${texto}")
```

- [ ] **Step 4: Verificar la sintaxis del workflow**

```bash
.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open('.github/workflows/contract-tests.yml',encoding='utf-8')); print('YAML valido')"
```

Esperado: `YAML valido`.

- [ ] **Step 5: Commit y push**

```bash
git add .github/workflows/contract-tests.yml
git commit -m "ci(contract): exigir las keys de FMP y AV, y distinguir el rate limit"
git push origin main
```

---

### Task 7: verificacion de punta a punta

**Files:** ninguno.

- [ ] **Step 1: Los cuatro gates locales**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ tests/integration/ -q
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
.venv/Scripts/python.exe -m ruff format --check src/ tests/ scripts/
.venv/Scripts/python.exe -m mypy src/ scripts/
```

Esperado: los cuatro en verde.

- [ ] **Step 2: Los contract tests enteros contra las APIs reales**

```bash
.venv/Scripts/python.exe -m pytest tests/contract/ -m contract -v
```

Esperado: pasan los de FRED, LLM, FMP y AV. El `-m contract` es obligatorio:
`pyproject.toml` define `addopts = "-m 'not contract'"` y sin sobrescribirlo se
deselecciona todo y sale con codigo 0 sin haber ejecutado un solo test.

Verificar en la salida que dice `N passed`, no `N deselected`.

- [ ] **Step 3: Lanzar el nightly a mano**

```bash
gh workflow run contract-tests.yml
sleep 20 && gh run list --workflow=contract-tests.yml --limit 1
```

Esperar a que termine y mirar el resultado:

```bash
gh run view <databaseId> --json status,conclusion,jobs -q '{status:.status,conclusion:.conclusion,jobs:[.jobs[]|{name,conclusion}]}'
```

Esperado: `success`. Confirmar ademas que los tests nuevos corrieron de verdad
en ese run, no que el job paso sin ejecutarlos:

```bash
gh run view <databaseId> --log | grep -E "test_fmp_contract|test_av_contract|passed"
```

Esperado: las lineas de los dos ficheros con `PASSED`.

---

## Como saber que termino

Las cinco, verificadas y no asumidas:

1. `pytest tests/unit/ tests/integration/ -q`, ruff (check y format) y mypy en verde.
2. `pytest tests/contract/ -m contract -v` pasa, y la salida dice `passed`, no `deselected`.
3. `gh secret list` muestra las seis credenciales.
4. Un run manual de `contract-tests.yml` termina en `success`, y su log muestra
   `test_fmp_contract` y `test_av_contract` con `PASSED`.
5. El validador rechaza un nivel a escala de ETF: el test
   `test_validate_weekly_close_rejects_an_etf_scale_level` pasa, y se lo vio
   fallar antes del arreglo.
