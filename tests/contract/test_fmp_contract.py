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
