from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MacroSnapshot(BaseModel):
    """
    Foto del contexto macro que acompaña al cierre semanal.

    Cada indicador viaja con su fecha de referencia: las series mensuales de
    FRED se publican con semanas de retraso, y presentarlas como si fueran del
    día del cierre sería engañoso.
    """

    model_config = ConfigDict(strict=True)

    cpi_yoy: float = Field(
        ..., description="Variación interanual del IPC (ej. 0.024 para 2.4%)"
    )
    cpi_as_of: date = Field(..., description="Fecha de la observación de IPC utilizada")
    unemployment_rate: float = Field(
        ..., description="Tasa de desempleo en porcentaje (ej. 4.1)"
    )
    unrate_as_of: date
    treasury_10y: float = Field(
        ..., description="Rendimiento del Treasury a 10 años en porcentaje"
    )
    dgs10_as_of: date


class WeeklyCloseData(BaseModel):
    """Esquema para el reporte de cierre semanal de mercados."""

    model_config = ConfigDict(strict=True)

    date: date
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
            "Precio de cierre del NASDAQ, o None si la fuente no cotiza el índice"
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
                "Los dos cierres vienen de la misma fuente y de la misma "
                "llamada: o están los dos, o no está ninguno."
            )
        return self


class MacroReleaseData(BaseModel):
    """Esquema para una publicación macroeconómica individual (ej. CPI, GDP, NFP)."""

    model_config = ConfigDict(strict=True)

    indicator_name: str
    date: date
    actual: float
    previous: float
    consensus: float | None = None
    units: str = Field(
        ..., description="Unidad de medida (ej. '%', 'Billions', 'Thousands')"
    )


class EarningReport(BaseModel):
    """Esquema para un reporte de ganancias individual."""

    model_config = ConfigDict(strict=True)

    symbol: str
    eps_actual: float
    eps_estimated: float | None = None
    revenue_actual: float | None = None
    revenue_estimated: float | None = None


class EarningsData(BaseModel):
    """Esquema consolidado para múltiples reportes de ganancias en un día o semana."""

    model_config = ConfigDict(strict=True)

    date: date
    reports: list[EarningReport]
