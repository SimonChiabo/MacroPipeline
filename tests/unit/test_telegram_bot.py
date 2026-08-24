import os
from unittest.mock import MagicMock, patch

import pytest

from macro_pipeline.telegram.bot import TelegramBot, TelegramBotError


@pytest.fixture
def tg_env():
    """Entorno minimo, con la guarda de operador explicitamente apagada.

    `TELEGRAM_ALLOWED_USER_ID` va vacia a proposito y no ausente: `patch.dict`
    anade sobre el entorno real, y `tests/contract/conftest.py` hace
    `load_dotenv` al importarse —aunque los contract tests esten
    deseleccionados—, asi que el `.env` del desarrollador se colaba y el
    resultado dependia de si tenia la variable puesta. Los tests que si
    ejercitan la guarda la sobreescriben.
    """
    return {
        "TELEGRAM_BOT_TOKEN": "test_token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_ALLOWED_USER_ID": "",
    }


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


@patch("requests.post")
def test_send_alert_posts_a_plain_message(mock_post, tg_env):
    """El aviso va por `sendMessage` y sin botones: no es algo que aprobar."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": {"message_id": 99}}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch.dict(os.environ, tg_env):
        entregado = TelegramBot().send_alert("El validador rechazó el titular")

    assert entregado is True
    endpoint, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert endpoint.endswith("/sendMessage")
    assert "El validador rechazó el titular" in kwargs["json"]["text"]
    assert "reply_markup" not in kwargs["json"]


@patch("requests.post")
def test_send_alert_does_not_raise_when_telegram_fails(mock_post, tg_env):
    """Un aviso que no se entrega no puede tirar abajo la run.

    El aviso es informativo: la publicación de la semana ya se decidió. Si
    Telegram esta caido, se devuelve False y queda en los logs, pero el
    pipeline sigue. Devolver un bool y no tragarse el fallo en silencio es lo
    que permite que quien llame sepa que el aviso no llego.
    """
    mock_post.side_effect = RuntimeError("connection reset")

    with patch.dict(os.environ, tg_env):
        entregado = TelegramBot().send_alert("Da igual el texto")

    assert entregado is False


@patch("requests.get")
@patch("requests.post")
def test_wait_for_approval_ignores_an_unauthorized_user(mock_post, mock_get, tg_env):
    """El boton de otro no aprueba.

    `TELEGRAM_ALLOWED_USER_ID` es lo unico que separa el HITL de ADR-004 de
    "cualquiera en el chat puede publicar en X y LinkedIn". Estuvo apagada en
    el `.env` real hasta el 2026-08-24 —solo lo decia un `logger.warning` al
    arrancar— y no tenia ni un test. Este es ese test.
    """
    mock_get_response = MagicMock()
    mock_get_response.json.return_value = {
        "result": [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb1",
                    "data": "approve_draft",
                    "from": {"id": 999999999},  # no es el operador autorizado
                    "message": {"message_id": 42},
                },
            }
        ]
    }
    mock_get_response.raise_for_status.return_value = None
    mock_get.return_value = mock_get_response
    mock_post.return_value = MagicMock()

    # El callback no autorizado se ignora, asi que nadie contesta y la espera
    # se agota. `wait_for_approval` senaliza eso levantando —el orquestador lo
    # atrapa y cierra la run como expirada—, no devolviendo False.
    with patch.dict(os.environ, {**tg_env, "TELEGRAM_ALLOWED_USER_ID": "8331472255"}):
        bot = TelegramBot()
        with pytest.raises(TelegramBotError, match="timeout"):
            bot.wait_for_approval(42, timeout_seconds=1)


@patch("requests.get")
@patch("requests.post")
def test_wait_for_approval_accepts_the_authorized_user(mock_post, mock_get, tg_env):
    """Y el del operador si: la guarda no puede bloquear a quien debe aprobar."""
    mock_get_response = MagicMock()
    mock_get_response.json.return_value = {
        "result": [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb1",
                    "data": "approve_draft",
                    "from": {"id": 8331472255},
                    "message": {"message_id": 42},
                },
            }
        ]
    }
    mock_get_response.raise_for_status.return_value = None
    mock_get.return_value = mock_get_response
    mock_post.return_value = MagicMock()

    with patch.dict(os.environ, {**tg_env, "TELEGRAM_ALLOWED_USER_ID": "8331472255"}):
        bot = TelegramBot()
        aprobado = bot.wait_for_approval(42, timeout_seconds=1)

    assert aprobado is True
