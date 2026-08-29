"""La capa LLM declarada auxiliar: sin key no participa, y no alerta.

ADR-009, tercer eje: un componente declarado opcional que no esta configurado
no participa, y no participar no es degradar. La declaracion es ADR-001, que
define la capa LLM como auxiliar — el LLM no toca numeros, solo redacta.

La asimetria con la API caida es a proposito: roto alerta, sin configurar no.
Las dos mitades se fijan en integration, no aca — este fichero solo llega al
constructor y nunca corre `run_weekly_close`:

- alerta: `test_a_dead_generator_alerts_even_if_the_validator_approves`, en
  `tests/integration/test_orchestrator_persistence.py`.
- silencio: `test_a_run_without_the_llm_layer_says_nothing`, en
  `tests/integration/test_orchestrator_exit_states.py`.
"""

from datetime import date

import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator, _generic_headline
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


# Las cuatro credenciales de los componentes que `__init__` construye *antes*
# de llegar a la capa LLM y que no tienen guarda: sin ellas revienta por otro
# motivo y el test no probaria nada. Son el resto de la limitacion (d) de
# ADR-009, que sigue abierta.
_REQUIRED = {
    "FMP_API_KEY": "fmp-test",
    "ALPHA_VANTAGE_API_KEY": "av-test",
    "TELEGRAM_BOT_TOKEN": "tg-test",
    "TELEGRAM_CHAT_ID": "123456",
}


@pytest.fixture
def buildable_env(monkeypatch, tmp_path):
    """Entorno minimo para construir un orquestador real.

    `STATE_DB_PATH` va a un temporal a proposito: sin eso `StateDB` crearia
    `~/.macropipeline/state.db` de verdad al correr los tests.

    Las delenv son obligatorias y no defensivas: `tests/contract/conftest.py`
    hace `load_dotenv()` al importarse, y pytest lo importa al recorrer
    `tests/` aunque los contract tests esten deseleccionados. Sin ellas el
    resultado depende de si la maquina tiene `.env`.
    """
    for name, value in _REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    for name in ("FRED_API_KEY", "PUBLISH_X", "PUBLISH_LINKEDIN"):
        monkeypatch.delenv(name, raising=False)


def test_a_missing_anthropic_key_does_not_kill_the_run(buildable_env, monkeypatch):
    """Sin key el constructor levantaba y la run moria antes de empezar.

    No habia alerta, no habia fila de estado, y la semana siguiente pasaba lo
    mismo en silencio. La tabla de ADR-009 declara que la capa LLM degrada, asi
    que tratarla como fatal en el constructor era la politica al reves.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    orch = MacroOrchestrator()

    assert orch.llm is None
    assert orch.validator_agent is None


def test_the_llm_layer_is_still_built_when_the_key_is_there(buildable_env, monkeypatch):
    """La otra direccion, y no es ceremonia.

    Sin este test, apagar la capa LLM siempre —o no construirla nunca— dejaria
    el de arriba en verde, y el pipeline publicaria el bloque generico todas
    las semanas con la key puesta.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    orch = MacroOrchestrator()

    assert orch.llm is not None
    assert orch.validator_agent is not None
