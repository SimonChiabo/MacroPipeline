"""El constructor no puede morir por una credencial ausente.

Limitacion (d) de ADR-009: hasta hoy `FMPClient`, `AlphaVantageClient`,
`TelegramBot` y las dos banderas levantaban `ValueError` sin que nadie lo
atrapara, asi que la run moria antes de `run_weekly_close` — sin alerta, sin
fila de estado, y repitiendose igual la semana siguiente.

La primera mitad del fichero no comprueba que la run haga algo util sin
credenciales: solo que el constructor sobreviva y deje escrito el motivo. Que
hacer con el motivo es del punto de decision, y se prueba en
`tests/integration/test_orchestrator_startup_gate.py`.

La segunda mitad es la consecuencia de haberlo vuelto total: si el constructor
ya no muere, `self.fmp` y `self.av` llegan al ETL pudiendo ser `None`, y las
guardas de `_fetch_weekly_close` tienen que nombrar la causa real en vez de
reventar con un `AttributeError`. Viven aca y no con el resto del ETL porque lo
que verifican es que el motivo que dejo escrito el constructor llegue entero
hasta la alerta.
"""

from unittest.mock import MagicMock

import pytest

from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FMP_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
)
from macro_pipeline.orchestration.main import MacroOrchestrator


@pytest.fixture
def data_orch():
    """Orquestador con lo justo para ejercitar `_fetch_weekly_close`."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch._allow_mock = False
    orch.component_errors = {}
    orch.switch_errors = {}
    orch.macro_error = None
    # Sin R2 no hay sincronizado: el pipeline corre contra el disco local,
    # que es como corrian estos tests antes de que el estado viajara.
    orch.state_sync = None
    orch.state_sync_error = None
    orch.fred = None
    orch.fmp = MagicMock()
    orch.av = MagicMock()
    return orch


# (componente, variables de entorno que hay que borrar para romperlo)
COMPONENTES = [
    ("fmp", ["FMP_API_KEY"]),
    ("av", ["ALPHA_VANTAGE_API_KEY"]),
    ("fred", ["FRED_API_KEY"]),
    ("anthropic", ["ANTHROPIC_API_KEY"]),
    ("r2", ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]),
    ("telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    ("x", ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]),
    ("linkedin", ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"]),
]

TODAS_LAS_CREDENCIALES = [v for _, variables in COMPONENTES for v in variables]

SWITCHES = [
    USE_FMP_VAR,
    USE_AV_VAR,
    USE_FRED_VAR,
    USE_ANTHROPIC_VAR,
    USE_R2_VAR,
    USE_TELEGRAM_VAR,
    PUBLISH_X_VAR,
    PUBLISH_LINKEDIN_VAR,
]


@pytest.fixture
def entorno_completo(monkeypatch, tmp_path):
    """Todas las credenciales presentes y ninguna bandera puesta.

    El `.env` del desarrollador se cuela en los unit tests —
    `tests/contract/conftest.py` hace `load_dotenv` al importarse aunque los
    contract tests esten deseleccionados—, asi que aca se fija el entorno
    entero a mano en vez de confiar en lo que haya.
    """
    for var in TODAS_LAS_CREDENCIALES:
        monkeypatch.setenv(var, "valor-de-prueba")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    for var in SWITCHES:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("componente,variables", COMPONENTES)
def test_a_missing_credential_does_not_kill_the_constructor(
    componente, variables, entorno_completo, monkeypatch
):
    for var in variables:
        monkeypatch.delenv(var, raising=False)

    orch = MacroOrchestrator()

    assert componente in orch.component_errors
    assert orch.component_errors[componente]
    # Igualdad y no pertenencia: con `in` a secas, una credencial ausente que
    # tumbara **tambien** a otro componente pasaria desapercibida, y la alerta
    # del punto de decision nombraria una cosa rota de mas.
    assert set(orch.component_errors) == {componente}
    assert orch.switch_errors == {}


def test_an_invalid_switch_does_not_kill_the_constructor_either(
    entorno_completo, monkeypatch
):
    """La otra mitad de (d): un `PUBLISH_X=yes` mataba la run igual de callado."""
    monkeypatch.setenv(PUBLISH_X_VAR, "yes")

    orch = MacroOrchestrator()

    assert PUBLISH_X_VAR in orch.switch_errors
    assert "yes" in orch.switch_errors[PUBLISH_X_VAR]
    # Lo que define a la rama: con la intencion ilegible **no se intenta**
    # construir. Sin estos dos asserts el test pasa igual contra un `_build`
    # que anota el motivo y construye el cliente igual, porque el fixture trae
    # las credenciales de X buenas.
    assert orch.x_client is None
    # Y los dos motivos son excluyentes para un mismo componente: es el
    # invariante del que depende el orden de las ramas del punto de decision.
    assert "x" not in orch.component_errors


def test_a_deliberate_switch_off_leaves_no_motive(entorno_completo, monkeypatch):
    """Apagado no es roto: sin motivo no hay nada que alertar."""
    monkeypatch.setenv(USE_FRED_VAR, "false")

    orch = MacroOrchestrator()

    assert orch.fred is None
    assert "fred" not in orch.component_errors


def test_everything_configured_leaves_both_dicts_empty(entorno_completo):
    orch = MacroOrchestrator()

    assert orch.component_errors == {}
    assert orch.switch_errors == {}
    assert orch.r2_ready is True
    assert orch.x_ready is True


def test_a_missing_av_names_itself_instead_of_an_attribute_error(data_orch):
    """La divergencia 1 otra vez: la alerta tiene que nombrar la causa real."""
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av = None
    data_orch.component_errors["av"] = "Se requiere ALPHA_VANTAGE_API_KEY."

    with pytest.raises(RuntimeError) as exc:
        data_orch._fetch_weekly_close()

    assert "ALPHA_VANTAGE_API_KEY" in str(exc.value)
    assert "NoneType" not in str(exc.value)
    # Y la que rompio primero, que es la accionable: sin ella la alerta habla
    # solo de la credencial que falta y el 503 hay que ir a buscarlo al log.
    assert "FMP 503" in str(exc.value)


def test_a_switched_off_av_says_so(data_orch):
    """Apagado a proposito y roto no son lo mismo, y el motivo los separa.

    Sin el `apagado` este test pasaria igual contra una guarda que dijera
    «Alpha Vantage no disponible» a secas: nombrar la variable no alcanza.
    """
    data_orch.fmp.get_historical_prices.side_effect = RuntimeError("FMP 503")
    data_orch.av = None

    with pytest.raises(RuntimeError) as exc:
        data_orch._fetch_weekly_close()

    assert USE_AV_VAR in str(exc.value)
    assert "apagado" in str(exc.value)


def test_the_etl_refuses_to_run_without_fmp(data_orch):
    """No lo alcanza ningun camino: el punto de decision aborta antes.

    Existe para que, si alguien reordena las ramas, esto muera con un motivo
    legible y no con un `AttributeError`.
    """
    data_orch.fmp = None

    with pytest.raises(RuntimeError, match="punto de decisión"):
        data_orch._fetch_weekly_close()
