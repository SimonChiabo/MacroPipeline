"""Contract tests de la API de FRED (ADR-008).

Verifican que el schema que devuelve FRED sigue siendo el que el pipeline
espera. No validan lógica propia —de eso se encargan los unit tests—, sino que
la forma de la respuesta externa no cambió bajo nuestros pies.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from macro_pipeline.data.fred_client import FREDClientError
from macro_pipeline.data.macro import (
    CPI_SERIES,
    DGS10_SERIES,
    UNRATE_SERIES,
    build_macro_snapshot,
)

pytestmark = pytest.mark.contract


def test_series_observations_returns_date_and_value(fred_client):
    """El hito de ADR-008: 'GDP' devuelve columnas 'date' y 'value' con datos.

    Comprobar solo los nombres de columna no alcanza: el cliente construye
    `df[['date','value']]` él mismo y devuelve un DataFrame vacío cuando la
    respuesta no trae observaciones. Sin `len(df) > 0` este test pasaría con
    FRED devolviendo un payload vacío.
    """
    df = fred_client.get_series_observations("GDP", observation_start="2015-01-01")

    assert list(df.columns) == ["date", "value"]
    assert len(df) > 0
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_numeric_dtype(df["value"])
    assert (df["value"] > 0).all()


@pytest.mark.parametrize(
    ("series_id", "low", "high"),
    [
        # Rangos deliberadamente amplios: detectan que la serie cambió de
        # unidad o de significado, no fluctuaciones normales del dato.
        (CPI_SERIES, 100.0, 1000.0),  # índice de nivel, base 1982-84 = 100
        (UNRATE_SERIES, 0.0, 30.0),  # porcentaje
        (DGS10_SERIES, -5.0, 25.0),  # porcentaje, puede ser negativo
    ],
)
def test_pipeline_series_stay_in_plausible_units(fred_client, series_id, low, high):
    """Las tres series que consume el cierre semanal siguen vivas y en su escala."""
    start = (date.today() - timedelta(days=480)).isoformat()
    df = fred_client.get_series_observations(series_id, observation_start=start)

    assert len(df) > 0, f"La serie {series_id} no devolvió observaciones."

    last = df.sort_values("date").iloc[-1]
    assert low <= last["value"] <= high, (
        f"{series_id} devolvió {last['value']}, fuera del rango esperado "
        f"[{low}, {high}]: puede haber cambiado de unidad."
    )
    # Una serie que dejó de actualizarse hace más de un año es tan rota como
    # una que devuelve error: el bloque macro quedaría mostrando datos viejos.
    assert last["date"].date() >= date.today() - timedelta(days=400)


def test_macro_snapshot_can_be_built_from_live_fred(fred_client):
    """La ventana de lookback sigue alcanzando para armar el snapshot completo.

    Es el contrato que de verdad importa: `_LOOKBACK_DAYS` tiene que cubrir el
    retraso con el que FRED publica el IPC. Si FRED se atrasa, esto se pone en
    rojo antes de que el bloque macro desaparezca en silencio un viernes.
    """
    snapshot = build_macro_snapshot(fred_client)

    assert -0.20 <= snapshot.cpi_yoy <= 0.30
    assert 0.0 < snapshot.unemployment_rate < 30.0
    assert -5.0 < snapshot.treasury_10y < 25.0
    assert snapshot.cpi_as_of <= date.today()


def test_unknown_series_raises_client_error(fred_client):
    """Una serie inexistente sigue siendo un error HTTP, no un DataFrame vacío."""
    with pytest.raises(FREDClientError):
        fred_client.get_series_observations("NO_EXISTE_ESTA_SERIE_XYZ")
