import os
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import TextBlock, ToolUseBlock

from macro_pipeline.llm.client import FALLBACK_HEADLINE, LLMClient
from macro_pipeline.llm.validator import (
    API_ERROR_REASON_PREFIX,
    TOOL_FAILURE_REASON,
    ValidatorAgent,
)


@pytest.fixture
def mock_anthropic():
    with patch("macro_pipeline.llm.client.Anthropic") as mock:
        yield mock


def test_llm_client_missing_key():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere ANTHROPIC_API_KEY"):
            LLMClient()


def test_generate_headline(mock_anthropic):
    # Setup mock
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    # TextBlock real y no un MagicMock: el cliente comprueba el tipo del
    # bloque antes de leer `.text`, y un doble no lo satisface.
    mock_response.content = [
        TextBlock(
            type="text",
            text='"El SP500 cierra la semana con un alza del 2.5%"',
        )
    ]
    mock_instance.messages.create.return_value = mock_response

    # Test
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        client = LLMClient()
        headline = client.generate_headline("SP500 Return: 2.5%")

    # Comprueba que le quite las comillas
    assert headline == "El SP500 cierra la semana con un alza del 2.5%"
    mock_instance.messages.create.assert_called_once()


@pytest.mark.parametrize(
    "raw_text",
    [
        '"El SP500 cierra la semana con un alza del 2.5%"',
        "**El SP500 cierra la semana con un alza del 2.5%**",
        '**"El SP500 cierra la semana con un alza del 2.5%"**',
        "*El SP500 cierra la semana con un alza del 2.5%*",
    ],
)
def test_generate_headline_strips_wrappers(mock_anthropic, raw_text):
    """Haiku 4.5 devuelve el titular en negrita markdown; no debe publicarse así."""
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [TextBlock(type="text", text=raw_text)]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        headline = LLMClient().generate_headline("SP500 Return: 2.5%")

    assert headline == "El SP500 cierra la semana con un alza del 2.5%"


def test_generate_headline_strips_partial_bold(mock_anthropic):
    """Negrita parcial: el envoltorio no abarca todo el titular."""
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [
        TextBlock(type="text", text="**El SP500** cierra con un alza del 2.5%")
    ]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        headline = LLMClient().generate_headline("SP500 Return: 2.5%")

    assert headline == "El SP500 cierra con un alza del 2.5%"
    assert "*" not in headline


def test_generate_headline_warns_over_length(mock_anthropic):
    """El límite de 120 chars es de producto (ADR-003) pero nada lo aplica:
    al menos tiene que quedar registrado cuando el modelo se pasa."""
    long_headline = "S" * 130
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [TextBlock(type="text", text=long_headline)]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        with patch("macro_pipeline.llm.client.logger") as mock_logger:
            headline = LLMClient().generate_headline("SP500 Return: 2.5%")

    assert headline == long_headline
    mock_logger.warning.assert_called_once_with("headline_over_length", length=130)


def test_validator_agent_approved(mock_anthropic):
    # Setup mock
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()

    # ToolUseBlock real: el agente comprueba el tipo del bloque antes de
    # leerlo, así que un MagicMock con `.type` puesto a mano no sirve.
    mock_response.content = [
        ToolUseBlock(
            type="tool_use",
            id="toolu_test_approved",
            name="submit_review",
            input={
                "approved": True,
                "reason": "Borrador preciso y fiel a los datos.",
            },
        )
    ]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        client = LLMClient()
        agent = ValidatorAgent(client)
        result = agent.review_draft("Draft text", "Source data")

    assert result["approved"] is True
    assert "reason" in result


def test_validator_agent_rejected_hallucination(mock_anthropic):
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()

    mock_response.content = [
        ToolUseBlock(
            type="tool_use",
            id="toolu_test_rejected",
            name="submit_review",
            input={
                "approved": False,
                "reason": (
                    "El borrador menciona crecimiento del 5% pero la fuente dice 2%."
                ),
            },
        )
    ]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        client = LLMClient()
        agent = ValidatorAgent(client)
        result = agent.review_draft("El PIB creció un 5%", "GDP Growth: 2%")

    assert result["approved"] is False
    assert "5%" in result["reason"]


# ── Caminos de fallback ────────────────────────────────────────────────────
# Los tres `except` que convierten un fallo de la API en algo que se parece a
# un pipeline sano. Son el motivo de que un modelo retirado pasara meses
# invisible, y son lo que los contract tests miran para no darse por buenos
# con la API caída. Aquí se fija la forma exacta de cada uno; que además
# lleguen al modelo lo comprueba `tests/contract/test_llm_contract.py`.


def test_generate_headline_falls_back_when_the_api_fails(mock_anthropic):
    """Un fallo de red devuelve el titular de emergencia, no una excepción."""
    mock_instance = mock_anthropic.return_value
    mock_instance.messages.create.side_effect = RuntimeError("connection reset")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        headline = LLMClient().generate_headline("SP500 Return: 2.5%")

    assert headline == FALLBACK_HEADLINE


def test_generate_headline_falls_back_on_non_text_first_block(mock_anthropic):
    """Un bloque `thinking` o `tool_use` primero no trae titular que extraer."""
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [
        ToolUseBlock(type="tool_use", id="toolu_x", name="lo_que_sea", input={})
    ]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        headline = LLMClient().generate_headline("SP500 Return: 2.5%")

    assert headline == FALLBACK_HEADLINE


def test_validator_rejects_when_the_api_fails(mock_anthropic):
    """Un error de API rechaza —cerrando hacia el lado seguro— pero se tiene
    que poder distinguir de un rechazo del modelo: el prefijo es el contrato
    del que dependen los contract tests."""
    mock_instance = mock_anthropic.return_value
    mock_instance.messages.create.side_effect = RuntimeError("HTTP 400")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        result = ValidatorAgent(LLMClient()).review_draft("Borrador", "Fuente")

    assert result["approved"] is False
    assert result["reason"].startswith(API_ERROR_REASON_PREFIX)
    assert "HTTP 400" in result["reason"]


def test_validator_rejects_when_the_model_skips_the_tool(mock_anthropic):
    """Sin `tool_use` en la respuesta no hay veredicto: se rechaza y se dice
    que el fallo fue sistémico, no del borrador."""
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [TextBlock(type="text", text="Me parece bien.")]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        result = ValidatorAgent(LLMClient()).review_draft("Borrador", "Fuente")

    assert result["approved"] is False
    assert result["reason"] == TOOL_FAILURE_REASON
