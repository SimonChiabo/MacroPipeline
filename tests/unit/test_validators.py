import pytest
from datetime import date
from pydantic import ValidationError as PydanticValidationError

from macro_pipeline.validators.schemas import WeeklyCloseData, MacroReleaseData
from macro_pipeline.validators.engine import ValidationEngine, ValidationError

@pytest.fixture
def engine(tmp_path):
    """Fixture que crea un archivo de reglas temporal para aislar las pruebas."""
    rules_content = """
weekly_close:
  sp500_return_min: -0.25
  sp500_return_max: 0.25
  nasdaq_return_min: -0.30
  nasdaq_return_max: 0.30

macro_release:
  gdp_growth_max_abs: 0.15
  unrate_max: 25.0
  unrate_min: 0.0
    """
    rules_file = tmp_path / "test_rules.yaml"
    rules_file.write_text(rules_content)
    return ValidationEngine(str(rules_file))

def test_pydantic_schema_strictness():
    """Prueba que los esquemas Pydantic fuercen tipos y restricciones (gt=0)."""
    with pytest.raises(PydanticValidationError, match="sp500_close"):
        WeeklyCloseData(
            date=date.today(),
            sp500_close=-100.0, # Inválido, debe ser > 0
            sp500_weekly_return=0.01,
            nasdaq_close=10000.0,
            nasdaq_weekly_return=0.02
        )

def test_validate_weekly_close_success(engine):
    data = WeeklyCloseData(
        date=date(2023, 1, 6),
        sp500_close=3900.0,
        sp500_weekly_return=0.05,
        nasdaq_close=11000.0,
        nasdaq_weekly_return=-0.02
    )
    assert engine.validate_weekly_close(data) is True

def test_validate_weekly_close_anomaly(engine):
    data = WeeklyCloseData(
        date=date(2023, 1, 6),
        sp500_close=3900.0,
        sp500_weekly_return=0.50, # 50% en una semana es anómalo según las reglas
        nasdaq_close=11000.0,
        nasdaq_weekly_return=0.02
    )
    with pytest.raises(ValidationError, match="Retorno del SP500 0.5 fuera del rango"):
        engine.validate_weekly_close(data)

def test_validate_macro_release_success(engine):
    data = MacroReleaseData(
        indicator_name="GDP",
        date=date(2023, 1, 1),
        actual=0.02, # 2% growth
        previous=0.015,
        units="%"
    )
    assert engine.validate_macro_release(data) is True

def test_validate_macro_release_anomaly(engine):
    data = MacroReleaseData(
        indicator_name="GDP",
        date=date(2023, 1, 1),
        actual=0.20, # 20% growth (abs > 0.15)
        previous=0.015,
        units="%"
    )
    with pytest.raises(ValidationError, match="Cambio en GDP del 0.2 parece poco realista"):
        engine.validate_macro_release(data)

def test_validate_unrate_anomaly(engine):
    data = MacroReleaseData(
        indicator_name="UNRATE",
        date=date(2023, 1, 1),
        actual=26.0, # Tasa > 25.0
        previous=5.0,
        units="%"
    )
    with pytest.raises(ValidationError, match="Tasa de desempleo 26.0 fuera de rangos"):
        engine.validate_macro_release(data)
