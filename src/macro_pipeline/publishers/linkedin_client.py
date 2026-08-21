import os
from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)


class LinkedInClientError(Exception):
    """Excepción para errores del cliente LinkedIn."""

    pass


class LinkedInClient:
    """Cliente para la API de LinkedIn (publicación UGC)."""

    BASE_URL = "https://api.linkedin.com/v2"

    def __init__(self, access_token: str | None = None, person_urn: str | None = None):
        self.access_token = access_token or os.environ.get("LINKEDIN_ACCESS_TOKEN")
        self.person_urn = person_urn or os.environ.get("LINKEDIN_PERSON_URN")

        if not self.access_token or not self.person_urn:
            raise ValueError(
                "Faltan credenciales de LinkedIn (LINKEDIN_ACCESS_TOKEN o "
                "LINKEDIN_PERSON_URN)."
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            }
        )

    def post_text(self, text: str) -> dict[str, Any]:
        """
        Publica texto plano en LinkedIn.
        """
        endpoint = f"{self.BASE_URL}/ugcPosts"

        payload = {
            # Formato: urn:li:person:12345 o urn:li:organization:67890
            "author": self.person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        logger.info("posting_linkedin", length=len(text))

        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error("linkedin_api_request_failed", error=str(e))
            raise LinkedInClientError(f"Error al publicar en LinkedIn: {e}") from e

        data = response.json()
        logger.info("linkedin_post_published", post_id=data.get("id"))
        return data
