import os
import pytest
from unittest.mock import patch, MagicMock
from macro_pipeline.llm.client import LLMClient
from macro_pipeline.llm.validator import ValidatorAgent

@pytest.fixture
def mock_anthropic():
    with patch('macro_pipeline.llm.client.Anthropic') as mock:
        yield mock

def test_llm_client_missing_key():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere ANTHROPIC_API_KEY"):
            LLMClient()

def test_generate_headline(mock_anthropic):
    # Setup mock
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='"El SP500 cierra la semana con un alza del 2.5%"')]
    mock_instance.messages.create.return_value = mock_response

    # Test
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        client = LLMClient()
        headline = client.generate_headline("SP500 Return: 2.5%")

    # Comprueba que le quite las comillas
    assert headline == 'El SP500 cierra la semana con un alza del 2.5%'
    mock_instance.messages.create.assert_called_once()

def test_validator_agent_approved(mock_anthropic):
    # Setup mock
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "submit_review"
    mock_block.input = {"approved": True, "reason": "Borrador preciso y fiel a los datos."}
    
    mock_response.content = [mock_block]
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
    
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "submit_review"
    mock_block.input = {"approved": False, "reason": "El borrador menciona crecimiento del 5% pero la fuente dice 2%."}
    
    mock_response.content = [mock_block]
    mock_instance.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        client = LLMClient()
        agent = ValidatorAgent(client)
        result = agent.review_draft("El PIB creció un 5%", "GDP Growth: 2%")

    assert result["approved"] is False
    assert "5%" in result["reason"]
