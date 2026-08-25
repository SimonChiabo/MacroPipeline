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
