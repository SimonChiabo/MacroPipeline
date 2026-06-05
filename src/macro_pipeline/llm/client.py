import os
import structlog
from typing import Optional
from anthropic import Anthropic

logger = structlog.get_logger(__name__)

# Versionar el prompt permite saber qué versión generó cada publicación histórica.
# Incrementar cuando cambie el contenido del system_prompt o la lógica de llamada.
HEADLINE_PROMPT_VERSION = "v1.0"


class LLMClient:
    """
    Cliente para la API de Claude (Anthropic). 
    Diseñado específicamente para generar texto auxiliar sin interferir con la lógica numérica.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Se requiere ANTHROPIC_API_KEY en el entorno.")
        self.client = Anthropic(api_key=self.api_key)
        # Usamos Haiku por rapidez y menor coste
        self.model = "claude-3-haiku-20240307" 

    def generate_headline(self, data_summary: str) -> str:
        """
        Genera un titular corto y profesional basado estrictamente en un resumen de datos.
        """
        system_prompt = (
            "Eres un analista financiero experto. Tu tarea es escribir un titular corto, "
            "profesional e impactante (máximo 120 caracteres) para un post de redes sociales "
            "basado estrictamente en los datos numéricos provistos. "
            "REGLA DE ORO: No inventes números bajo ninguna circunstancia. Usa sólo los provistos."
        )
        
        logger.info("generating_headline", model=self.model)
        
        try:
            response = self.client.messages.create(
                    model=self.model,
                    max_tokens=150,
                    temperature=0.2,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": f"Datos numéricos confirmados:\n{data_summary}\n\nGenera el titular:"}
                    ]
                )
            headline = response.content[0].text.strip()
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
            
        # Limpieza básica por si el LLM pone comillas
        if headline.startswith('"') and headline.endswith('"'):
            headline = headline[1:-1]
            
        logger.info("headline_generated", length=len(headline))
        return headline
