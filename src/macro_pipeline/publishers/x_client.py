import os
import structlog
from typing import Optional, Dict, Any
from requests_oauthlib import OAuth1Session

logger = structlog.get_logger(__name__)

class XClientError(Exception):
    """Excepción para errores del cliente X."""
    pass

class XClient:
    """Cliente para interactuar con la API v2 de X (Twitter)."""
    BASE_URL = "https://api.twitter.com/2"

    def __init__(self, 
                 api_key: Optional[str] = None, 
                 api_secret: Optional[str] = None,
                 access_token: Optional[str] = None,
                 access_secret: Optional[str] = None):
        
        # Leer variables de entorno si no se proveen
        self.api_key = api_key or os.environ.get("X_API_KEY")
        self.api_secret = api_secret or os.environ.get("X_API_SECRET")
        self.access_token = access_token or os.environ.get("X_ACCESS_TOKEN")
        self.access_secret = access_secret or os.environ.get("X_ACCESS_SECRET")
        
        if not all([self.api_key, self.api_secret, self.access_token, self.access_secret]):
            raise ValueError("Faltan credenciales de X API. Revisa las variables de entorno.")
            
        self.session = OAuth1Session(
            self.api_key,
            client_secret=self.api_secret,
            resource_owner_key=self.access_token,
            resource_owner_secret=self.access_secret
        )

    def post_tweet(self, text: str) -> Dict[str, Any]:
        """
        Publica un tweet de texto plano.
        """
        endpoint = f"{self.BASE_URL}/tweets"
        payload = {"text": text}
        
        logger.info("posting_tweet", length=len(text))
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            # Capturar status code o texto de error si es posible
            status = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
            logger.error("x_api_request_failed", error=str(e), status_code=status)
            raise XClientError(f"Error al publicar en X: {e}") from e
            
        data = response.json()
        logger.info("tweet_posted", tweet_id=data.get("data", {}).get("id"))
        return data
