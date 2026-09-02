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
from macro_pipeline.validators.schemas import MacroSnapshot, WeeklyCloseData


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
    titular = _generic_headline(_data(), data_source="fmp")

    assert "+1.20%" in titular
    assert "-1.90%" in titular
    assert "S&P 500" in titular
    assert "Nasdaq Composite" in titular


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


def _macro() -> MacroSnapshot:
    return MacroSnapshot(
        cpi_yoy=0.0336,
        cpi_as_of=date(2026, 7, 1),
        unemployment_rate=4.1,
        unrate_as_of=date(2026, 7, 1),
        treasury_10y=4.75,
        dgs10_as_of=date(2026, 8, 31),
    )


def test_the_deterministic_headline_carries_everything_the_image_shows():
    """Con el LLM apagado este texto es el post, no la degradación de un post.

    Mientras fue la rama de degradación alcanzaba con que llevara las dos
    cifras de mercado. Al pasar a ser el copy publicado tiene que decir lo
    mismo que la imagen: nivel, fecha del dato y el bloque macro con su
    referencia temporal. Los formatos son los de la plantilla a propósito —un
    mismo post no puede escribir el mismo número de dos maneras.
    """
    titular = _generic_headline(_data_con_macro(), data_source="fmp")

    assert "2026-08-21" in titular
    assert "S&P 500: 5,100.00 (+1.20% semanal)" in titular
    assert "Nasdaq Composite: 16,000.00 (-1.90% semanal)" in titular
    assert "IPC interanual: +3.4% (07/2026)" in titular
    assert "Desempleo: 4.1% (07/2026)" in titular
    assert "Treasury 10A: 4.75% (al 31/08/2026)" in titular
    assert "Fuentes: FMP, FRED" in titular


def test_the_deterministic_headline_omits_the_level_it_must_not_publish():
    """Sin nivel publicable el retorno se queda solo, igual que en la tarjeta.

    Es ADR-009 divergencia 4 en el texto: por la ruta de Alpha Vantage el
    nivel es el del ETF, y publicarlo bajo la etiqueta del índice sería la
    invariante de ADR-001 rota en el copy en vez de en la imagen.
    """
    data = _data_con_macro().model_copy(
        update={"sp500_close": None, "nasdaq_close": None}
    )

    titular = _generic_headline(data, data_source="av")

    assert "S&P 500: +1.20% semanal" in titular
    assert "Nasdaq Composite: -1.90% semanal" in titular
    assert "5,100" not in titular
    assert "16,000" not in titular


def test_the_deterministic_headline_names_the_source_the_data_came_from():
    """La atribución sigue al dato, no al camino feliz.

    Escribir «FMP» sobre cifras que trajo Alpha Vantage es exactamente la
    etiqueta equivocada que `docs/data-dictionary.md` persigue, y encima en el
    único lugar del post donde se prometen fuentes.
    """
    data = _data_con_macro().model_copy(
        update={"sp500_close": None, "nasdaq_close": None}
    )

    titular = _generic_headline(data, data_source="av")

    assert "Fuentes: Alpha Vantage, FRED" in titular
    assert "FMP" not in titular


def test_the_deterministic_headline_drops_the_macro_block_when_there_is_none():
    """FRED caído no puede dejar el post prometiendo una fuente que no aportó."""
    titular = _generic_headline(_data(), data_source="fmp")

    assert "IPC" not in titular
    assert "Desempleo" not in titular
    assert "Treasury" not in titular
    assert "Fuentes: FMP" in titular
    assert "FRED" not in titular


def test_the_deterministic_headline_fits_in_a_tweet():
    """280 es el límite duro de X (ADR-003), y el emoji cuenta doble ahí.

    El texto es de formato fijo y sus números están acotados, así que esto se
    puede fijar con un test en vez de con una guarda en ejecución. Se mide
    contra el caso más ancho: todo presente y cifras de cinco dígitos.
    """
    ancho = _data_con_macro().model_copy(
        update={"sp500_close": 99999.99, "nasdaq_close": 99999.99}
    )

    titular = _generic_headline(ancho, data_source="fmp")

    assert len(titular) + titular.count("📊") <= 280, (
        f"el titular determinista mide {len(titular)} y no entra en un tweet"
    )


def _data_con_macro() -> WeeklyCloseData:
    return _data().model_copy(update={"macro": _macro()})
