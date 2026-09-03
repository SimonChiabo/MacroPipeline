"""Infraestructura compartida por los contract tests.

Estos tests pegan contra las APIs reales, así que necesitan credenciales. La
regla de resolución es deliberada: en local se saltan si falta la key (para no
romperle el `pytest` a nadie que no tenga cuenta de FRED), pero en CI faltar
una key es un fallo ruidoso. Un run enteramente saltado sale con código 0, y
eso reportaría el nightly en verde sin haber verificado ningún contrato, que es
exactamente lo que ADR-008 existe para evitar.

**Las credenciales no pasan por `os.environ`.** Antes esto hacía `load_dotenv`
a nivel de módulo, y pytest importa esta conftest aunque `-m 'not contract'`
deseleccione todos sus tests: el `.env` local terminaba en el entorno del
proceso y llegaba a tests que no tenían nada que ver. Con `USE_ANTHROPIC=false`
en el `.env`, eso tumbaba un test unitario del orquestador, y sólo en la
máquina de quien tuviera ese `.env` — en CI y en un clon limpio pasaba.

Restaurar `os.environ` después no arregla este caso: la carga ocurría en el
import, antes de que exista fixture alguna. Por eso el fichero se lee a un
diccionario que viaja por fixture, y nadie escribe en el entorno.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from dotenv import dotenv_values

# Comodidad para correr los contract tests en local: en CI las credenciales
# llegan por el entorno (GitHub Secrets) y no hay .env que cargar.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def leer_credenciales() -> Mapping[str, str | None]:
    """El `.env` local como diccionario, sin tocar `os.environ`.

    Un fichero ausente es el caso normal en CI y en un clon recién hecho, así
    que devuelve un diccionario vacío en vez de levantar.
    """
    if not ENV_FILE.is_file():
        return {}
    return dotenv_values(ENV_FILE)


def require_api_key(credenciales: Mapping[str, str | None], name: str) -> str:
    """Devuelve la credencial; salta en local y falla en CI si no está.

    El entorno le gana al fichero, que es la precedencia que ya tenía
    `load_dotenv` sin `override`: en CI mandan los GitHub Secrets, y un `.env`
    olvidado en el runner no puede taparlos.
    """
    key = os.environ.get(name) or credenciales.get(name)
    if key:
        return key

    if os.environ.get("CI"):
        pytest.fail(
            f"{name} no está definida en CI. Los contract tests no pueden "
            f"saltarse silenciosamente: revisar los GitHub Secrets."
        )

    pytest.skip(f"{name} no definida: contract test omitido en local.")


@pytest.fixture(scope="session")
def credenciales() -> Mapping[str, str | None]:
    """Las credenciales del `.env` local, leídas una vez por sesión."""
    return leer_credenciales()


@pytest.fixture(scope="session")
def fred_client(credenciales):
    """Cliente FRED real, apuntando a la API de producción."""
    from macro_pipeline.data.fred_client import FREDClient

    return FREDClient(api_key=require_api_key(credenciales, "FRED_API_KEY"))


@pytest.fixture(scope="session")
def llm_client(credenciales):
    """Cliente de Anthropic real, contra la API de producción."""
    from macro_pipeline.llm.client import LLMClient

    return LLMClient(api_key=require_api_key(credenciales, "ANTHROPIC_API_KEY"))


@pytest.fixture(scope="session")
def validator_agent(llm_client):
    """Agente validador real (ADR-001), montado sobre el cliente de sesión."""
    from macro_pipeline.llm.validator import ValidatorAgent

    return ValidatorAgent(llm_client)


@pytest.fixture(scope="session")
def fmp_client(credenciales):
    """Cliente FMP real, apuntando a la API de producción."""
    from macro_pipeline.data.fmp_client import FMPClient

    return FMPClient(api_key=require_api_key(credenciales, "FMP_API_KEY"))


@pytest.fixture(scope="session")
def av_client(credenciales):
    """Cliente Alpha Vantage real.

    `scope="session"` no es cosmético acá: la capa gratuita tiene un throttle
    por minuto y cada llamada cuesta cuota, así que los tests comparten una
    sola respuesta (ver la fixture `spy_daily` en `test_av_contract.py`).
    """
    from macro_pipeline.data.av_client import AlphaVantageClient

    return AlphaVantageClient(
        api_key=require_api_key(credenciales, "ALPHA_VANTAGE_API_KEY")
    )
