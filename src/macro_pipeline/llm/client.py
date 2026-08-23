import os

import structlog
from anthropic import Anthropic
from anthropic.types import TextBlock

logger = structlog.get_logger(__name__)

# El id del modelo vive aquí y no dentro de `__init__` para poder persistirlo
# junto a la versión de prompt: un titular histórico no se puede reproducir
# sabiendo solo qué prompt lo generó.
MODEL = "claude-haiku-4-5"

# Versionar el prompt permite saber qué versión generó cada publicación histórica.
# Incrementar cuando cambie el contenido del system_prompt o la lógica de llamada.
# v1.1: migración a anthropic 1.x — cambia el modelo, cómo se pasa
# `temperature`, y se pide texto plano (Haiku 4.5 devolvía markdown).
# v1.2: el prompt seguía escrito para Sonnet 3.5 / Haiku 3. Se baja el
# volumen de la regla numérica, se añade el contexto que solo conoce el
# autor (canal, lector, listón de tono) y se quita "impactante", que
# empujaba justo al tono que el validador rechaza.
HEADLINE_PROMPT_VERSION = "v1.2"


class LLMClient:
    """
    Cliente para la API de Claude (Anthropic).
    Diseñado específicamente para generar texto auxiliar sin interferir con
    la lógica numérica.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Se requiere ANTHROPIC_API_KEY en el entorno.")
        self.client = Anthropic(api_key=self.api_key)
        # Usamos Haiku por rapidez y menor coste. `claude-3-haiku-20240307`
        # pasó su fecha de retiro (2026-04-19); 4.5 es la Haiku vigente.
        self.model = MODEL

    def generate_headline(self, data_summary: str) -> str:
        """
        Genera un titular corto y profesional basado estrictamente en un
        resumen de datos.
        """
        system_prompt = (
            "Escribes el titular del cierre semanal de mercado que se "
            "publica en la cuenta de X y en la página de LinkedIn del "
            "proyecto. Lo lee gente del sector financiero: el registro es "
            "informativo y sobrio, nunca alarmista ni promocional, y nada "
            "en el titular puede leerse como recomendación de inversión.\n"
            "Escribe en español, en una sola línea de máximo 120 "
            "caracteres.\n"
            "Los números del resumen ya vienen calculados y verificados: "
            "úsalos tal cual y no añadas ninguna cifra que no aparezca en "
            "él. Un número inventado en un post financiero no se puede "
            "corregir una vez publicado.\n"
            "Devuelve texto plano: sin markdown, sin asteriscos y sin "
            "comillas envolviendo el titular."
        )

        logger.info("generating_headline", model=self.model)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=150,
                # anthropic 1.x sacó `temperature` de la firma de
                # `messages.create()`, pero la API de Haiku 4.5 lo sigue
                # aceptando: `extra_body` se mezcla tal cual en el JSON.
                extra_body={"temperature": 0.2},
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Datos numéricos confirmados:\n{data_summary}\n\n"
                            "Genera el titular:"
                        ),
                    }
                ],
            )
            block = response.content[0]
            if not isinstance(block, TextBlock):
                # thinking / tool_use en la primera posición: no hay
                # titular que extraer. Cae al fallback de más abajo,
                # igual que un error de red.
                raise TypeError(
                    f"La respuesta de Anthropic empieza con un bloque "
                    f"{block.type}, no con texto."
                )
            headline = block.text.strip()
            # Tracking de coste: loggear usage por cada llamada
            logger.info(
                "llm_usage",
                model=self.model,
                prompt_version=HEADLINE_PROMPT_VERSION,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        except Exception as e:
            logger.warning("anthropic_api_failed_using_mock_headline", error=str(e))
            headline = "Cierre Semanal: Resumen del Mercado"

        # Limpieza básica de envoltorios. Haiku 4.5 tiende a devolver el
        # titular en negrita markdown (`**...**`), que Haiku 3 no ponía y
        # que se publicaría con los asteriscos a la vista en X y LinkedIn.
        # Se itera porque los envoltorios se combinan: `**"titular"**`.
        for wrapper in ("**", '"', "*"):
            while (
                headline.startswith(wrapper)
                and headline.endswith(wrapper)
                and len(headline) > 2 * len(wrapper)
            ):
                headline = headline[len(wrapper) : -len(wrapper)].strip()

        # Negrita parcial (`**S&P 500** sube 2.5%`): el bucle de arriba solo
        # quita envoltorios completos. Ningún titular legítimo lleva `**`.
        headline = headline.replace("**", "")

        # El límite de 120 caracteres es una decisión de producto (ADR-003:
        # un solo titular sirve a todos los canales), pero nada lo hacía
        # cumplir: `max_tokens=150` deja sitio para varias veces esa
        # longitud. No se trunca —cortar un titular a media palabra es peor
        # que publicarlo largo—, se deja visible en los logs.
        if len(headline) > 120:
            logger.warning("headline_over_length", length=len(headline))

        logger.info("headline_generated", length=len(headline))
        return headline
