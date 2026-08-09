from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import date

class MacroSnapshot(BaseModel):
    """
    Foto del contexto macro que acompaña al cierre semanal.

    Cada indicador viaja con su fecha de referencia: las series mensuales de
    FRED se publican con semanas de retraso, y presentarlas como si fueran del
    día del cierre sería engañoso.
    """
    model_config = ConfigDict(strict=True)

    cpi_yoy: float = Field(..., description="Variación interanual del IPC (ej. 0.024 para 2.4%)")
    cpi_as_of: date = Field(..., description="Fecha de la observación de IPC utilizada")
    unemployment_rate: float = Field(..., description="Tasa de desempleo en porcentaje (ej. 4.1)")
    unrate_as_of: date
    treasury_10y: float = Field(..., description="Rendimiento del Treasury a 10 años en porcentaje")
    dgs10_as_of: date

class WeeklyCloseData(BaseModel):
    """Esquema para el reporte de cierre semanal de mercados."""
    model_config = ConfigDict(strict=True)

    date: date
    sp500_close: float = Field(..., gt=0, description="Precio de cierre del SP500")
    sp500_weekly_return: float = Field(..., description="Retorno semanal del SP500 (ej. 0.05 para 5%)")
    nasdaq_close: float = Field(..., gt=0, description="Precio de cierre del NASDAQ")
    nasdaq_weekly_return: float = Field(..., description="Retorno semanal del NASDAQ")
    macro: Optional[MacroSnapshot] = Field(
        default=None,
        description="Contexto macro opcional: si FRED falla, el cierre se publica sin él"
    )

class MacroReleaseData(BaseModel):
    """Esquema para una publicación macroeconómica individual (ej. CPI, GDP, NFP)."""
    model_config = ConfigDict(strict=True)
    
    indicator_name: str
    date: date
    actual: float
    previous: float
    consensus: Optional[float] = None
    units: str = Field(..., description="Unidad de medida (ej. '%', 'Billions', 'Thousands')")

class EarningReport(BaseModel):
    """Esquema para un reporte de ganancias individual."""
    model_config = ConfigDict(strict=True)
    
    symbol: str
    eps_actual: float
    eps_estimated: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimated: Optional[float] = None

class EarningsData(BaseModel):
    """Esquema consolidado para múltiples reportes de ganancias en un día o semana."""
    model_config = ConfigDict(strict=True)
    
    date: date
    reports: List[EarningReport]
