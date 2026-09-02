from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError

from macro_pipeline.data.macro import CPI_SERIES, DGS10_SERIES, UNRATE_SERIES
from macro_pipeline.validators.engine import ValidationEngine, ValidationError
from macro_pipeline.validators.schemas import (
    MacroReleaseData,
    MacroSnapshot,
    WeeklyCloseData,
)


def _snapshot(**overrides):
    """Snapshot macro fresco respecto al 'today' usado en los tests (2026-08-09)."""
    base = dict(
        cpi_yoy=0.024,
        cpi_as_of=date(2026, 6, 1),
        unemployment_rate=4.1,
        unrate_as_of=date(2026, 7, 1),
        treasury_10y=4.69,
        dgs10_as_of=date(2026, 8, 6),
    )
    base.update(overrides)
    return MacroSnapshot(**base)


@pytest.fixture
def engine(tmp_path):
    """Fixture que crea un archivo de reglas temporal para aislar las pruebas."""
    rules_content = """
weekly_close:
  sp500_return_min: -0.25
  sp500_return_max: 0.25
  nasdaq_return_min: -0.30
  nasdaq_return_max: 0.30
  sp500_close_min: 2000
  sp500_close_max: 30000
  nasdaq_close_min: 5000
  nasdaq_close_max: 100000

macro_release:
  gdp_growth_max_abs: 0.15
  unrate_max: 25.0
  unrate_min: 0.0

macro_snapshot:
  cpi_yoy_min: -0.05
  cpi_yoy_max: 0.20
  unrate_min: 0.0
  unrate_max: 25.0
  dgs10_min: 0.0
  dgs10_max: 20.0
  cpi_max_staleness_days: 90
  unrate_max_staleness_days: 75
  dgs10_max_staleness_days: 10
    """
    rules_file = tmp_path / "test_rules.yaml"
    rules_file.write_text(rules_content)
    return ValidationEngine(str(rules_file))


def test_pydantic_schema_strictness():
    """Prueba que los esquemas Pydantic fuercen tipos y restricciones (gt=0)."""
    with pytest.raises(PydanticValidationError, match="sp500_close"):
        WeeklyCloseData(
            date=date.today(),
            sp500_close=-100.0,  # Inválido, debe ser > 0
            sp500_weekly_return=0.01,
            nasdaq_close=10000.0,
            nasdaq_weekly_return=0.02,
        )


def test_validate_weekly_close_success(engine):
    data = WeeklyCloseData(
        date=date(2023, 1, 6),
        sp500_close=3900.0,
        sp500_weekly_return=0.05,
        nasdaq_close=11000.0,
        nasdaq_weekly_return=-0.02,
    )
    assert engine.validate_weekly_close(data) is True


def test_validate_weekly_close_anomaly(engine):
    data = WeeklyCloseData(
        date=date(2023, 1, 6),
        sp500_close=3900.0,
        sp500_weekly_return=0.50,  # 50% en una semana es anómalo según las reglas
        nasdaq_close=11000.0,
        nasdaq_weekly_return=0.02,
    )
    with pytest.raises(ValidationError, match="Retorno del SP500 0.5 fuera del rango"):
        engine.validate_weekly_close(data)


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


def test_validate_macro_release_success(engine):
    data = MacroReleaseData(
        indicator_name="GDP",
        date=date(2023, 1, 1),
        actual=0.02,  # 2% growth
        previous=0.015,
        units="%",
    )
    assert engine.validate_macro_release(data) is True


def test_validate_macro_release_anomaly(engine):
    data = MacroReleaseData(
        indicator_name="GDP",
        date=date(2023, 1, 1),
        actual=0.20,  # 20% growth (abs > 0.15)
        previous=0.015,
        units="%",
    )
    with pytest.raises(
        ValidationError, match="Cambio en GDP del 0.2 parece poco realista"
    ):
        engine.validate_macro_release(data)


def test_validate_unrate_anomaly(engine):
    data = MacroReleaseData(
        indicator_name="UNRATE",
        date=date(2023, 1, 1),
        actual=26.0,  # Tasa > 25.0
        previous=5.0,
        units="%",
    )
    with pytest.raises(ValidationError, match="Tasa de desempleo 26.0 fuera de rangos"):
        engine.validate_macro_release(data)


# ── Snapshot macro (FRED) ────────────────────────────────────────────────────

TODAY = date(2026, 8, 9)


def test_validate_macro_snapshot_success(engine):
    assert engine.validate_macro_snapshot(_snapshot(), today=TODAY) is True


def test_validate_macro_snapshot_accepts_cpi_at_normal_release_lag(engine):
    """El CPI publicado va ~2 meses atrasado: eso es normal, no debe rechazarse."""
    assert (
        engine.validate_macro_snapshot(
            _snapshot(cpi_as_of=date(2026, 6, 1)), today=TODAY
        )
        is True
    )


def test_validate_macro_snapshot_rejects_stale_cpi(engine):
    """Un CPI de hace más de 90 días indica serie discontinuada o error de ingesta."""
    with pytest.raises(ValidationError, match=CPI_SERIES):
        engine.validate_macro_snapshot(
            _snapshot(cpi_as_of=date(2026, 1, 1)), today=TODAY
        )


def test_validate_macro_snapshot_rejects_stale_treasury(engine):
    with pytest.raises(ValidationError, match="DGS10"):
        engine.validate_macro_snapshot(
            _snapshot(dgs10_as_of=date(2026, 6, 1)), today=TODAY
        )


def test_validate_macro_snapshot_rejects_impossible_unrate(engine):
    with pytest.raises(ValidationError, match="desempleo"):
        engine.validate_macro_snapshot(_snapshot(unemployment_rate=42.0), today=TODAY)


def test_validate_macro_snapshot_rejects_hyperinflation_reading(engine):
    """Un IPC interanual del 300% en EEUU es un error de datos, no una noticia."""
    with pytest.raises(ValidationError, match="IPC"):
        engine.validate_macro_snapshot(_snapshot(cpi_yoy=3.0), today=TODAY)


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
    with pytest.raises(PydanticValidationError, match="misma fuente"):
        WeeklyCloseData(
            date=date(2026, 8, 21),
            sp500_close=7657.71,
            sp500_weekly_return=0.012,
            nasdaq_close=None,
            nasdaq_weekly_return=0.019,
        )


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


def test_validate_weekly_close_sin_niveles_sigue_validando_el_retorno_del_nasdaq(
    engine,
):
    """El gemelo del de arriba, del lado del NASDAQ.

    El chequeo del SP500 corre primero y corta, asi que sin este test una
    guarda de nivel colada en la rama del NASDAQ dejaria la suite entera verde.
    Verificado por mutacion el 2026-09-01: condicionar ese rango a
    `nasdaq_close is not None` no ponia rojo a nadie.
    """
    data = WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=None,
        sp500_weekly_return=0.012,
        nasdaq_close=None,
        nasdaq_weekly_return=0.80,  # +80% en una semana: imposible
    )
    with pytest.raises(ValidationError, match="Retorno del NASDAQ"):
        engine.validate_weekly_close(data)


def test_stale_macro_alerts_name_the_series_the_etl_actually_requests(engine):
    """El aviso de serie vieja nombra la serie que el ETL pide, no una copia.

    El nombre estaba escrito a mano en `engine.py` y el ETL lo tiene en
    `data/macro.py`. Mientras coincidieron nadie lo notó; cuando el IPC pasó a
    `CPIAUCNS` la alerta siguió diciendo `CPIAUCSL`, que es mandar al operador
    a mirar una serie que el pipeline ni consulta —la misma lección que
    `safe_build_macro_snapshot` ya había aprendido con los motivos de fallo.

    Se recorren las tres para que el acoplamiento quede fijado en las tres, y
    no sólo en la que se rompió esta vez.
    """
    casos = [
        (CPI_SERIES, {"cpi_as_of": date(2026, 1, 1)}),
        (UNRATE_SERIES, {"unrate_as_of": date(2026, 1, 1)}),
        (DGS10_SERIES, {"dgs10_as_of": date(2026, 1, 1)}),
    ]
    for series_id, override in casos:
        with pytest.raises(ValidationError, match=series_id):
            engine.validate_macro_snapshot(_snapshot(**override), today=TODAY)
