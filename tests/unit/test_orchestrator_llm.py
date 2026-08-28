"""La capa LLM declarada auxiliar: sin key no participa, y no alerta.

ADR-009, tercer eje: un componente declarado opcional que no esta configurado
no participa, y no participar no es degradar. La declaracion es ADR-001, que
define la capa LLM como auxiliar — el LLM no toca numeros, solo redacta.

La asimetria con la API caida es a proposito y esta cubierta por
`test_a_dead_generator_alerts_even_if_the_validator_approves` en
`tests/integration/test_orchestrator_persistence.py`: roto alerta, sin
configurar no.
"""

from datetime import date

from macro_pipeline.orchestration.main import _generic_headline
from macro_pipeline.validators.schemas import WeeklyCloseData


def _data() -> WeeklyCloseData:
    return WeeklyCloseData(
        date=date(2026, 8, 21),
        sp500_close=5100.0,
        sp500_weekly_return=0.012,
        nasdaq_close=16000.0,
        nasdaq_weekly_return=-0.019,
        macro=None,
    )


def test_the_generic_headline_carries_both_real_returns():
    """La premisa con la que ADR-009 acepta degradar aca.

    "El bloque generico lleva las cifras reales — las pone el pipeline, no el
    modelo". Si el texto pierde una cifra, esa premisa deja de ser cierta y la
    degradacion pasa a costar informacion en vez de solo redaccion.
    """
    titular = _generic_headline(_data())

    assert "+1.20%" in titular
    assert "-1.90%" in titular
    assert "S&P500" in titular
    assert "NASDAQ" in titular
