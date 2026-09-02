"""El primer test directo de `_fetch_weekly_close`.

Hasta hoy esta función no tenía ninguno: los tests de integración la mockean
entera, así que la ventana de cinco días hábiles, la supresión del nivel por
fuente y el cálculo del retorno nunca se ejercitaron desde acá.

El caso que la trae a cobertura es el del 2026-09-02: el endpoint
`historical-price-eod/full` de FMP devuelve una fila para la sesión **en
curso**, cuyo `close` es el último precio negociado. Medido ese día sobre
`^IXIC`: 26.211,996 a las 14:40 UTC y 26.196,812 a las 14:59, con la misma
fecha. El pipeline publicó el primero rotulado «Cierre».
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd

from macro_pipeline.orchestration.main import MacroOrchestrator

# Valor imposible para una sesión real: si aparece publicado, la fila de la
# sesión en curso se coló.
_PRECIO_INTRADIA = 99999.0


def _precios(fechas: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(fechas), "close": closes})


def _orquestador(df: pd.DataFrame) -> MacroOrchestrator:
    """Lo mínimo que `_fetch_weekly_close` toca, y nada más."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch._allow_mock = False
    orch.fmp = MagicMock()
    orch.fmp.get_historical_prices.return_value = df
    orch.av = MagicMock()
    orch._fetch_macro_snapshot = MagicMock(return_value=None)
    return orch


def _catorce_dias_terminando_hoy() -> tuple[list[date], list[float]]:
    """Catorce días consecutivos, el último es hoy: la sesión sin terminar.

    Fechas consecutivas y no `bdate_range` a propósito: con días hábiles, un
    test corrido en sábado no tendría fila de hoy y pasaría sin ejercitar
    nada.
    """
    hoy = date.today()
    fechas = [hoy - timedelta(days=n) for n in range(13, -1, -1)]
    closes = [100.0 + i for i in range(13)] + [_PRECIO_INTRADIA]
    return fechas, closes


def test_the_session_in_progress_is_never_published_as_a_close():
    """Sólo se publican sesiones terminadas.

    La fila de hoy existe mientras el mercado opera y su `close` cambia minuto
    a minuto. Publicarla es la invariante de ADR-001 rota en el ETL: una cifra
    correcta —es el precio real de ese instante— bajo una etiqueta que promete
    otra cosa.
    """
    fechas, closes = _catorce_dias_terminando_hoy()
    orch = _orquestador(_precios(fechas, closes))

    data, _ = orch._fetch_weekly_close()

    assert data.date == date.today() - timedelta(days=1)
    assert data.sp500_close == 112.0
    assert data.sp500_close != _PRECIO_INTRADIA
    assert data.nasdaq_close != _PRECIO_INTRADIA


def test_the_weekly_return_ignores_the_session_in_progress():
    """El retorno también se calcula contra sesiones terminadas.

    Sin esto la guarda sería cosmética: el nivel saldría correcto y el retorno
    seguiría midiendo contra un precio de media mañana.
    """
    fechas, closes = _catorce_dias_terminando_hoy()
    orch = _orquestador(_precios(fechas, closes))

    data, _ = orch._fetch_weekly_close()

    # Ninguna combinación con el precio intradía puede dar un retorno chico:
    # 99999 contra ~110 daría casi +900 %.
    assert abs(data.sp500_weekly_return) < 0.5
