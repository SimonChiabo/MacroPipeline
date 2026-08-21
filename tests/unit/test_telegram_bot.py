import os
from unittest.mock import MagicMock, patch

import pytest

from macro_pipeline.telegram.bot import TelegramBot


@pytest.fixture
def tg_env():
    return {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "123456789"}


def test_telegram_init_missing_keys():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Faltan credenciales"):
            TelegramBot()


@patch("requests.post")
def test_send_approval_request_text(mock_post, tg_env):
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": {"message_id": 42}}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch.dict(os.environ, tg_env):
        bot = TelegramBot()
        msg_id = bot.send_approval_request("Draft de prueba")

    assert msg_id == 42
    mock_post.assert_called_once()


@patch("requests.post")
def test_send_approval_request_with_image(mock_post, tg_env):
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": {"message_id": 43}}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch.dict(os.environ, tg_env):
        bot = TelegramBot()
        msg_id = bot.send_approval_request(
            "Draft con imagen", image_bytes=b"fake_image_bytes"
        )

    assert msg_id == 43
    mock_post.assert_called_once()


@patch("requests.get")
@patch("requests.post")
def test_wait_for_approval_success(mock_post, mock_get, tg_env):
    mock_get_response = MagicMock()
    # Simula que al hacer polling obtenemos un update respondiendo al mensaje 42
    mock_get_response.json.return_value = {
        "result": [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb1",
                    "data": "approve_draft",
                    "message": {"message_id": 42},
                },
            }
        ]
    }
    mock_get_response.raise_for_status.return_value = None
    mock_get.return_value = mock_get_response

    mock_post.return_value = MagicMock()

    with patch.dict(os.environ, tg_env):
        bot = TelegramBot()
        # Timeout ultra corto ya que mockeamos la respuesta
        is_approved = bot.wait_for_approval(42, timeout_seconds=1)

    assert is_approved is True
    # Deberían haber 2 posts de mantenimiento
    # (answerCallbackQuery y editMessageReplyMarkup)
    assert mock_post.call_count == 2
