"""El constructor no puede morir por una credencial ausente.

Limitacion (d) de ADR-009: hasta hoy `FMPClient`, `AlphaVantageClient`,
`TelegramBot` y las dos banderas levantaban `ValueError` sin que nadie lo
atrapara, asi que la run moria antes de `run_weekly_close` — sin alerta, sin
fila de estado, y repitiendose igual la semana siguiente.

Estos tests no comprueban que la run haga algo util sin credenciales: solo que
el constructor sobreviva y deje escrito el motivo. Que hacer con el motivo es
del punto de decision, y se prueba en
`tests/integration/test_orchestrator_startup_gate.py`.
"""

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
