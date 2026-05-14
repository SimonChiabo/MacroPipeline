from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import date

class WeeklyCloseData(BaseModel):
    """Esquema para el reporte de cierre semanal de mercados."""
    model_config = ConfigDict(strict=True)
    
    date: date
    sp500_close: float = Field(..., gt=0, description="Precio de cierre del SP500")
    sp500_weekly_return: float = Field(..., description="Retorno semanal del SP500 (ej. 0.05 para 5%)")
    nasdaq_close: float = Field(..., gt=0, description="Precio de cierre del NASDAQ")
    nasdaq_weekly_return: float = Field(..., description="Retorno semanal del NASDAQ")

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
