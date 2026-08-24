from typing import Any, cast

import structlog
from anthropic.types import (
    MessageParam,
    ToolChoiceToolParam,
    ToolParam,
    ToolUseBlock,
)

from macro_pipeline.llm.client import LLMClient

logger = structlog.get_logger(__name__)

# Versionar el prompt del validador permite rastrear qué criterios de rechazo
# estaban activos para cada publicación histórica.
# v1.1: migración a anthropic 1.x — cambia el modelo y cómo se pasa `temperature`.
# v1.2: el prompt seguía escrito para Sonnet 3.5 / Haiku 3. Se baja el volumen
# (las mayúsculas imperativas ya no hacen falta y sesgan hacia rechazar), se
# explicita qué cuenta como fiel —redondeos y separadores en español— para no
# rechazar titulares correctos, y la tool pasa a `strict`.
VALIDATOR_PROMPT_VERSION = "v1.2"

# Los dos veredictos que el agente fabrica sin haber llegado al modelo. Son
# constantes y no literales enterrados en los `return` porque un rechazo real
# y una llamada muerta son indistinguibles desde fuera: ambos devuelven
# `approved=False`. El contract test mira estos prefijos para saber si el
# validador rechazó de verdad o si la API se cayó y el fallback lo tapó.
TOOL_FAILURE_REASON = (
    "Fallo sistémico: El LLM no utilizó el esquema estructurado requerido."
)
API_ERROR_REASON_PREFIX = "Error interno al conectar con LLM:"


class ValidatorAgent:
    """
    Agente de revisión (segunda opinión) utilizando tool-use para forzar
    una respuesta estructurada (JSON). Actúa como filtro final de texto.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def review_draft(self, draft_text: str, source_data: str) -> dict[str, Any]:
        """
        Revisa que el borrador coincida con los datos fuente sin alucinaciones.
        Fuerza la salida a través de una tool predefinida (JSON).
        """
        system_prompt = (
            "Eres el control de riesgos previo a publicar el titular del "
            "cierre semanal en X y en LinkedIn. Comparas el borrador con "
            "los datos fuente, que son la única verdad disponible: ya "
            "vienen calculados y verificados por el pipeline.\n"
            "Rechaza el borrador si cita una cifra que no está en la fuente "
            "o si la tergiversa, y recházalo si recomienda invertir o si el "
            "tono es alarmista o promocional. Un rechazo descarta el "
            "titular y publica en su lugar un texto genérico, así que "
            "rechaza por un problema real y no por preferencia de estilo.\n"
            "Cuentan como fieles a la fuente: redondear (2.53% -> 2.5%), "
            "escribir la misma cifra en notación española (5,100.00 -> "
            "5.100,00, o 5.100 si además se redondea), omitir alguno de "
            "los datos, y describir la dirección del movimiento sin "
            "repetir el número."
        )

        tool_schema: ToolParam = {
            "name": "submit_review",
            "description": (
                "Registra el veredicto de la revisión del borrador. Es la "
                "única salida de este agente: el pipeline lee `approved` "
                "para decidir si publica el titular generado o lo sustituye "
                "por un texto genérico, y guarda `reason` en SQLite para "
                "poder auditar después por qué se rechazó. Llámala una sola "
                "vez y con el veredicto ya decidido: no devuelve nada, no "
                "hay forma de corregirlo luego, y no se ejecuta ninguna "
                "otra acción a partir de ella."
            ),
            # `strict` hace que la API garantice que `input` valida contra el
            # esquema, en vez de confiar en que el modelo lo respete.
            # Soportado en Haiku 4.5. Exige `additionalProperties: false`.
            "strict": True,
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "approved": {
                        "type": "boolean",
                        "description": (
                            "True si el borrador es 100% fiel a los datos y "
                            "no da consejos financieros. False si hay "
                            "alucinaciones numéricas o tono inadecuado."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Explicación breve de la decisión tomada "
                            "(máx 1 o 2 oraciones)."
                        ),
                    },
                },
                "required": ["approved", "reason"],
            },
        }

        tool_choice: ToolChoiceToolParam = {
            "type": "tool",
            "name": "submit_review",
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
                # Determinismo máximo. Va por `extra_body` porque anthropic
                # 1.x sacó `temperature` de la firma; la API de Haiku 4.5 lo
                # sigue aceptando y el valor llega igual en el JSON.
                extra_body={"temperature": 0.0},
                system=system_prompt,
                tools=[tool_schema],
                tool_choice=tool_choice,
                messages=[MessageParam(role="user", content=user_content)],
            )

            for block in response.content:
                if isinstance(block, ToolUseBlock) and block.name == "submit_review":
                    # El SDK tipa `input` como object porque depende del
                    # esquema; el nuestro fuerza un objeto de dos campos.
                    result = cast(dict[str, Any], block.input)
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
            return {"approved": False, "reason": TOOL_FAILURE_REASON}

        except Exception as e:
            logger.error("validator_agent_api_error", error=str(e))
            return {
                "approved": False,
                "reason": f"{API_ERROR_REASON_PREFIX} {e}",
            }
