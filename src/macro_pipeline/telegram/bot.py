import os
import time
import requests
import structlog
import json
from typing import Optional

logger = structlog.get_logger(__name__)

class TelegramBotError(Exception):
    """Excepción específica para errores de Telegram."""
    pass

class TelegramBot:
    """Cliente para interactuar con la API de Telegram. Gestiona el flujo HITL (Human In The Loop)."""
    
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        
        if not self.token or not self.chat_id:
            raise ValueError("Faltan credenciales TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
            
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_approval_request(self, text: str, image_bytes: Optional[bytes] = None) -> int:
        """
        Envía un borrador (opcionalmente con imagen) al chat configurado, 
        adjuntando los botones inline de Aprobar / Rechazar.
        Devuelve el ID del mensaje para poder identificar las respuestas (callbacks).
        """
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprobar Publicacion", "callback_data": "approve_draft"},
                    {"text": "❌ Rechazar / Descartar", "callback_data": "reject_draft"}
                ]
            ]
        }
        
        try:
            if image_bytes:
                # Enviar como foto
                endpoint = f"{self.base_url}/sendPhoto"
                data = {
                    "chat_id": self.chat_id,
                    "caption": text,
                    "reply_markup": json.dumps(reply_markup)
                }
                files = {"photo": ("draft.png", image_bytes, "image/png")}
                response = requests.post(endpoint, data=data, files=files, timeout=15)
            else:
                # Enviar solo texto
                endpoint = f"{self.base_url}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "reply_markup": reply_markup
                }
                response = requests.post(endpoint, json=payload, timeout=10)
                
            response.raise_for_status()
            res_data = response.json()
            message_id = res_data["result"]["message_id"]
            logger.info("telegram_approval_request_sent", message_id=message_id)
            return message_id
            
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))
            raise TelegramBotError(f"Error enviando mensaje a Telegram: {e}") from e

    def wait_for_approval(self, message_id: int, timeout_seconds: int = 600) -> bool:
        """
        Realiza Long-Polling esperando que el usuario presione un botón.
        Bloquea la ejecución hasta que se apruebe, se rechace, o caduque el tiempo.
        """
        endpoint = f"{self.base_url}/getUpdates"
        offset = None
        start_time = time.time()
        
        logger.info("telegram_waiting_for_approval", timeout=timeout_seconds, message_id=message_id)
        
        while time.time() - start_time < timeout_seconds:
            # Long polling de 10s para no ahogar la red
            params = {"timeout": 10, "allowed_updates": ["callback_query"]} 
            if offset:
                params["offset"] = offset
                
            try:
                response = requests.get(endpoint, params=params, timeout=15)
                response.raise_for_status()
                updates = response.json().get("result", [])
                
                for update in updates:
                    offset = update["update_id"] + 1 # Avanzar el cursor
                    
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_msg_id = cb["message"]["message_id"]
                        
                        # Si respondieron a nuestro mensaje específico
                        if cb_msg_id == message_id:
                            action = cb["data"]
                            
                            # 1. Responder al callback para que el botón deje de cargar
                            requests.post(f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                            
                            # 2. Quitar los botones para que no se pueda pulsar dos veces
                            requests.post(f"{self.base_url}/editMessageReplyMarkup", json={
                                "chat_id": self.chat_id,
                                "message_id": message_id,
                                "reply_markup": {"inline_keyboard": []} 
                            })
                            
                            if action == "approve_draft":
                                logger.info("telegram_draft_approved", message_id=message_id)
                                return True
                            elif action == "reject_draft":
                                logger.warning("telegram_draft_rejected", message_id=message_id)
                                return False
                                
            except requests.exceptions.RequestException as e:
                # En caso de error de red transitorio, solo loggeamos e intentamos de nuevo
                logger.error("telegram_polling_network_error", error=str(e))
                time.sleep(2)
                
            # Pequeña pausa entre polls si la respuesta fue instantánea
            time.sleep(0.5)
            
        logger.error("telegram_approval_timeout")
        raise TelegramBotError("Tiempo de espera para aprobación en Telegram agotado (timeout).")
