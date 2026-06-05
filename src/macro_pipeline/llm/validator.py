import structlog
from typing import Dict, Any
from macro_pipeline.llm.client import LLMClient

logger = structlog.get_logger(__name__)

# Versionar el prompt del validador permite rastrear qué criterios de rechazo
# estaban activos para cada publicación histórica.
VALIDATOR_PROMPT_VERSION = "v1.0"


class ValidatorAgent:
    """
    Agente de revisión (segunda opinión) utilizando tool-use para forzar 
    una respuesta estructurada (JSON). Actúa como filtro final de texto.
    """
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def review_draft(self, draft_text: str, source_data: str) -> Dict[str, Any]:
        """
        Revisa que el borrador coincida con los datos fuente sin alucinaciones.
        Fuerza la salida a través de una tool predefinida (JSON).
        """
        system_prompt = (
            "Eres el Auditor de Riesgos del pipeline. Debes comparar el borrador del post con los datos fuente. "
            "1. Si el borrador contiene números que NO están en la fuente o los tergiversa, DEBES RECHAZARLO. "
            "2. Si el tono es excesivamente sensacionalista o parece dar un consejo financiero, RECHÁZALO. "
            "3. En otro caso, apruébalo."
        )

        tool_schema = {
            "name": "submit_review",
            "description": "Envia el resultado de la revisión del borrador generado.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "approved": {
                        "type": "boolean",
                        "description": "True si el borrador es 100% fiel a los datos y no da consejos financieros. False si hay alucinaciones numéricas o tono inadecuado."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explicación breve de la decisión tomada (máx 1 o 2 oraciones)."
                    }
                },
                "required": ["approved", "reason"]
            }
        }

        user_content = (
            f"DATOS FUENTE VERIFICADOS:\n{source_data}\n\n"
            f"BORRADOR A REVISAR:\n{draft_text}\n"
        )
        
        logger.info("validator_agent_running")
        
        try:
            response = self.llm.client.messages.create(
                model=self.llm.model,
                max_tokens=300,
                temperature=0.0, # Determinismo máximo
                system=system_prompt,
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": "submit_review"},
                messages=[{"role": "user", "content": user_content}]
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_review":
                    result = block.input
                    # Tracking de coste del agente validador
                    logger.info(
                        "validator_usage",
                        prompt_version=VALIDATOR_PROMPT_VERSION,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        approved=result.get("approved"),
                    )
                    return result
                    
            # Fallback de seguridad si no usó la tool adecuadamente
            logger.error("validator_agent_failed_tool_use")
            return {"approved": False, "reason": "Fallo sistémico: El LLM no utilizó el esquema estructurado requerido."}
            
        except Exception as e:
            logger.error("validator_agent_api_error", error=str(e))
            return {"approved": False, "reason": f"Error interno al conectar con LLM: {e}"}
