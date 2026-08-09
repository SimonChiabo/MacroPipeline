"""Construcción del snapshot macroeconómico a partir de series de FRED."""

from datetime import date
from typing import Optional, Tuple

import pandas as pd
import structlog

from macro_pipeline.validators.schemas import MacroSnapshot

logger = structlog.get_logger(__name__)

# Series de FRED que alimentan el bloque macro del cierre semanal
CPI_SERIES = "CPIAUCSL"    # Índice de precios al consumidor (nivel, no variación)
UNRATE_SERIES = "UNRATE"   # Tasa de desempleo (%)
DGS10_SERIES = "DGS10"     # Rendimiento del Treasury a 10 años (%)

# Ventana solicitada a FRED: debe cubrir con holgura los 13 meses que necesita
# el cálculo interanual, incluso con series mensuales publicadas con retraso.
_LOOKBACK_DAYS = 420


class MacroDataError(Exception):
    """Los datos macro no alcanzan para construir un snapshot confiable."""
    pass


def compute_yoy(df: pd.DataFrame) -> Tuple[float, date]:
    """
    Calcula la variación interanual de una serie de nivel (ej. el IPC).

    Compara la última observación contra la más reciente anterior a 12 meses
    atrás, no contra "12 filas antes": las series de FRED tienen huecos y
    revisiones, y contar filas produciría comparaciones contra el mes equivocado.

    Retorna (variación como fracción, fecha de la última observación).
    """
    if df is None or df.empty:
        raise MacroDataError("Serie vacía: no hay observaciones para calcular la variación.")

    df = df.sort_values("date")
    last = df.iloc[-1]
    cutoff = last["date"] - pd.DateOffset(months=12)

    previous = df[df["date"] <= cutoff]
    if previous.empty:
        raise MacroDataError(
            "La serie no cubre 12 meses hacia atrás: no se puede calcular la variación interanual."
        )

    base = previous.iloc[-1]
    if base["value"] == 0:
        raise MacroDataError("Valor base cero: variación interanual indefinida.")

    yoy = (last["value"] - base["value"]) / base["value"]
    return float(yoy), last["date"].date()


def _latest_observation(df: pd.DataFrame, series_id: str) -> Tuple[float, date]:
    """Devuelve (valor, fecha) de la última observación de una serie de nivel."""
    if df is None or df.empty:
        raise MacroDataError(f"Serie {series_id} sin observaciones.")

    last = df.sort_values("date").iloc[-1]
    return float(last["value"]), last["date"].date()


def build_macro_snapshot(fred, today: Optional[date] = None) -> MacroSnapshot:
    """
    Construye el snapshot macro consultando las tres series a FRED.
    Lanza MacroDataError si alguna serie no alcanza para un dato confiable.
    """
    today = today or date.today()
    observation_start = (today - pd.Timedelta(days=_LOOKBACK_DAYS)).isoformat()

    cpi_yoy, cpi_as_of = compute_yoy(
        fred.get_series_observations(CPI_SERIES, observation_start=observation_start)
    )
    unemployment_rate, unrate_as_of = _latest_observation(
        fred.get_series_observations(UNRATE_SERIES, observation_start=observation_start),
        UNRATE_SERIES,
    )
    treasury_10y, dgs10_as_of = _latest_observation(
        fred.get_series_observations(DGS10_SERIES, observation_start=observation_start),
        DGS10_SERIES,
    )

    snapshot = MacroSnapshot(
        cpi_yoy=cpi_yoy,
        cpi_as_of=cpi_as_of,
        unemployment_rate=unemployment_rate,
        unrate_as_of=unrate_as_of,
        treasury_10y=treasury_10y,
        dgs10_as_of=dgs10_as_of,
    )
    logger.info(
        "macro_snapshot_built",
        cpi_yoy=snapshot.cpi_yoy,
        cpi_as_of=snapshot.cpi_as_of.isoformat(),
        unemployment_rate=snapshot.unemployment_rate,
        treasury_10y=snapshot.treasury_10y,
    )
    return snapshot


def safe_build_macro_snapshot(fred, today: Optional[date] = None) -> Optional[MacroSnapshot]:
    """
    Versión tolerante a fallos: devuelve None en lugar de propagar.

    El bloque macro es complementario al cierre de mercado. Que FRED esté caído
    o que una serie venga corta no debe abortar la publicación semanal.
    """
    try:
        return build_macro_snapshot(fred, today=today)
    except Exception as e:
        logger.warning("macro_snapshot_unavailable", error=str(e), error_type=type(e).__name__)
        return None
