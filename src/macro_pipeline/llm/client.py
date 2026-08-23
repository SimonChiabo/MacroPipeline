import os

import structlog
from anthropic import Anthropic
from anthropic.types import TextBlock

logger = structlog.get_logger(__name__)

# Versionar el prompt permite saber qué versión generó cada publicación histórica.
# Incrementar cuando cambie el contenido del system_prompt o la lógica de llamada.
# v1.1: migración a anthropic 1.x — cambia el modelo, cómo se pasa
# `temperature`, y se pide texto plano (Haiku 4.5 devolvía markdown).
HEADLINE_PROMPT_VERSION = "v1.1"


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
        self.model = "claude-haiku-4-5"

    def generate_headline(self, data_summary: str) -> str:
        """
        Genera un titular corto y profesional basado estrictamente en un
        resumen de datos.
        """
        system_prompt = (
            "Eres un analista financiero experto. Tu tarea es escribir un "
            "titular corto, profesional e impactante (máximo 120 caracteres) "
            "para un post de redes sociales basado estrictamente en los datos "
            "numéricos provistos. REGLA DE ORO: No inventes números bajo "
            "ninguna circunstancia. Usa sólo los provistos. "
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

        logger.info("headline_generated", length=len(headline))
        return headline
