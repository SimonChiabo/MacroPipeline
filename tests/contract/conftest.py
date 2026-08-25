"""Infraestructura compartida por los contract tests.

Estos tests pegan contra las APIs reales, así que necesitan credenciales. La
regla de resolución es deliberada: en local se saltan si falta la key (para no
romperle el `pytest` a nadie que no tenga cuenta de FRED), pero en CI faltar
una key es un fallo ruidoso. Un run enteramente saltado sale con código 0, y
eso reportaría el nightly en verde sin haber verificado ningún contrato, que es
exactamente lo que ADR-008 existe para evitar.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Comodidad para correr los contract tests en local: en CI las credenciales
# llegan por el entorno (GitHub Secrets) y no hay .env que cargar.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def require_api_key(name: str) -> str:
    """Devuelve la credencial; salta en local y falla en CI si no está."""
    key = os.environ.get(name)
    if key:
        return key

    if os.environ.get("CI"):
        pytest.fail(
            f"{name} no está definida en CI. Los contract tests no pueden "
            f"saltarse silenciosamente: revisar los GitHub Secrets."
        )

    pytest.skip(f"{name} no definida: contract test omitido en local.")


@pytest.fixture(scope="session")
def fred_client():
    """Cliente FRED real, apuntando a la API de producción."""
    from macro_pipeline.data.fred_client import FREDClient

    return FREDClient(api_key=require_api_key("FRED_API_KEY"))


@pytest.fixture(scope="session")
def llm_client():
    """Cliente de Anthropic real, contra la API de producción."""
    from macro_pipeline.llm.client import LLMClient

    return LLMClient(api_key=require_api_key("ANTHROPIC_API_KEY"))


@pytest.fixture(scope="session")
def validator_agent(llm_client):
    """Agente validador real (ADR-001), montado sobre el cliente de sesión."""
    from macro_pipeline.llm.validator import ValidatorAgent

    return ValidatorAgent(llm_client)


@pytest.fixture(scope="session")
def fmp_client():
    """Cliente FMP real, apuntando a la API de producción."""
    from macro_pipeline.data.fmp_client import FMPClient

    return FMPClient(api_key=require_api_key("FMP_API_KEY"))


@pytest.fixture(scope="session")
def av_client():
    """Cliente Alpha Vantage real.

    `scope="session"` no es cosmético acá: la capa gratuita tiene un throttle
    por minuto y cada llamada cuesta cuota, así que los tests comparten una
    sola respuesta (ver la fixture `spy_daily` en `test_av_contract.py`).
    """
    from macro_pipeline.data.av_client import AlphaVantageClient

    return AlphaVantageClient(api_key=require_api_key("ALPHA_VANTAGE_API_KEY"))
