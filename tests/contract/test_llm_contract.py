"""Contract tests de la API de Anthropic (ADR-008 aplicado a ADR-001).

Es la capa que los cuatro gates de CI no pueden ver. `ruff`, `mypy` y los unit
tests trabajan sobre mocks: un modelo retirado, un parámetro que la API dejó de
aceptar o un prompt que empezó a aprobar lo que debía rechazar salen todos
verdes. Eso ya pasó una vez —`claude-3-haiku-20240307` estuvo retirado durante
meses sin que nada se pusiera rojo— y estos tests son la única red que lo ve.

Tres contratos, en orden de gravedad:

1. **El validador sigue rechazando una cifra inventada.** Es el guardián de
   ADR-001: el LLM no toca números, y el único mecanismo que lo comprueba en
   caliente es este agente. El prompt v1.2 aflojó tres cosas a la vez —bajó el
   volumen, explicitó qué redondeos son fieles, y avisó de que rechazar tiene
   coste— y las tres empujan hacia aprobar.
2. **La API acepta el esquema `strict`.** Es el cambio de v1.2 capaz de
   devolver 400 en producción con todo lo demás en verde.
3. **El titular que genera el pipeline pasa su propio validador.** Encadenar
   las dos llamadas es lo único que ve un desacuerdo entre los dos prompts, y
   ahí apareció el fallo que motivó v1.3: con el bloque macro completo, el
   generador etiquetaba el desempleo como IPC y su propio validador lo
   rechazaba, así que el pipeline publicaba el texto genérico 9 de cada 10
   semanas. Ningún test con mocks puede ver eso: cada mock devuelve lo que le
   pusimos. v1.3 reescribió la cláusula culpable y v1.4 bajó `temperature` a
   0.0, con lo que este test dejó de ser una lotería: para una misma forma de
   datos, o pasa siempre o falla siempre.

Cuidado al leer los asserts: los dos puntos de entrada del pipeline capturan
`Exception` y devuelven algo plausible (`FALLBACK_HEADLINE`, o un rechazo con
`approved=False`). Una llamada muerta y un rechazo legítimo son idénticos desde
fuera, así que cada test comprueba además que la respuesta vino del modelo.
"""

import pytest

from macro_pipeline.llm.client import FALLBACK_HEADLINE
from macro_pipeline.llm.validator import (
    API_ERROR_REASON_PREFIX,
    TOOL_FAILURE_REASON,
)

pytestmark = pytest.mark.contract

# Misma forma que arma `WeeklyPipeline._run` antes de llamar al LLM
# (`orchestration/main.py`): notación inglesa, porcentajes con signo y el
# bloque macro de FRED debajo. Un contract test contra un formato inventado
# verificaría un prompt que producción nunca ve.
SOURCE_DATA = (
    "SP500: Cierre 5,100.00 (Retorno Semanal: +2.53%)\n"
    "NASDAQ: Cierre 16,200.00 (Retorno Semanal: -1.20%)\n"
    "Contexto macro (FRED):\n"
    "IPC interanual: +3.1% (dato de 07/2026)\n"
    "Desempleo: 4.2% (dato de 07/2026)\n"
    "Treasury 10 años: 4.35% (dato de 22/08/2026)"
)

# Segunda forma de datos para el circuito completo. Con `temperature` en 0.0
# (v1.4) el titular de una fuente dada es fijo, así que un único SOURCE_DATA
# haría que el test end-to-end dejara de muestrear: pasaría siempre porque la
# entrada nunca cambia. El fallo que motivó v1.3 dependía de la forma de los
# datos —dos porcentajes de escala parecida con etiquetas distintas—, así que
# la segunda semana invierte los signos y acerca IPC y desempleo. Verificada
# como determinista: 8/8 titulares idénticos a 0.0.
SOURCE_DATA_OTRA_SEMANA = (
    "SP500: Cierre 4,880.50 (Retorno Semanal: -0.87%)\n"
    "NASDAQ: Cierre 15,410.20 (Retorno Semanal: +1.94%)\n"
    "Contexto macro (FRED):\n"
    "IPC interanual: +2.4% (dato de 06/2026)\n"
    "Desempleo: 3.9% (dato de 06/2026)\n"
    "Treasury 10 años: 4.02% (dato de 15/08/2026)"
)


def assert_veredicto_real(result: dict) -> None:
    """El veredicto lo emitió el modelo y no el `except` del agente.

    Sin esto, un 400 por el esquema `strict` haría pasar todos los tests de
    rechazo: el fallback de seguridad también devuelve `approved=False`.
    """
    reason = result.get("reason", "")
    assert reason != TOOL_FAILURE_REASON, (
        "El modelo no usó la tool: el veredicto es el fallback de seguridad, "
        "no una revisión real."
    )
    assert not reason.startswith(API_ERROR_REASON_PREFIX), (
        f"La llamada a Anthropic falló y el rechazo es el fallback: {reason}"
    )


def test_strict_tool_schema_is_accepted_by_the_api(validator_agent):
    """La tool `strict` de v1.2 no devuelve 400 y la salida valida el esquema.

    `strict: true` exige `additionalProperties: false` y `required` completo.
    Si la API dejara de aceptar la combinación, el pipeline seguiría corriendo
    —el `except` convierte el 400 en un rechazo— y publicaría el texto genérico
    todas las semanas sin que nada se pusiera rojo.
    """
    result = validator_agent.review_draft(
        "El S&P 500 cierra la semana al alza", SOURCE_DATA
    )

    assert_veredicto_real(result)
    # La garantía que aporta `strict`: exactamente estas dos claves, con estos
    # tipos. Sin `strict` el modelo podía añadir campos o cambiar el tipo.
    assert set(result) == {"approved", "reason"}
    assert isinstance(result["approved"], bool)
    assert isinstance(result["reason"], str)
    assert result["reason"].strip()


@pytest.mark.parametrize(
    ("caso", "borrador"),
    [
        # Cifra que no sale de ningún dato de la fuente.
        ("cifra_inventada", "El S&P 500 sube un 3,8% en la semana"),
        # Serie que la fuente no menciona: el número es plausible y no
        # contradice nada, que es justo lo que lo hace peligroso.
        (
            "serie_ausente",
            "El S&P 500 sube un 2,5% con el déficit comercial en 68.000 millones",
        ),
        # El caso que apunta al aflojamiento de v1.2: "los redondeos cuentan
        # como fieles" no puede haberse generalizado a cualquier cifra parecida.
        ("falso_redondeo", "El S&P 500 avanza un 2,8% en la semana"),
        # Un cierre inventado se lee como dato duro y no como estimación.
        ("cierre_inventado", "El S&P 500 cierra en 5.400 puntos"),
        # Etiqueta cambiada: el 4,2% está en la fuente, pero es el desempleo.
        # Ninguna cifra es inventada, así que ADR-001 en su letra se cumple;
        # lo que se rompe es la invariante que nunca se escribió: un número
        # conserva el indicador del que salió. Es el fallo real que encontró
        # este contract test (el generador v1.2 lo producía en 4/6 titulares),
        # y va aquí para que el rechazo quede como contrato y no como suerte.
        ("etiqueta_cambiada", "El IPC interanual se sitúa en el 4,2%"),
    ],
)
def test_rejects_invented_figures(validator_agent, caso, borrador):
    """El guardián de ADR-001 sigue vivo: una cifra que no está en la fuente
    no llega a publicarse."""
    result = validator_agent.review_draft(borrador, SOURCE_DATA)

    assert_veredicto_real(result)
    assert result["approved"] is False, (
        f"El validador aprobó un borrador con {caso}: {borrador!r}. "
        f"Motivo que dio: {result['reason']!r}"
    )


@pytest.mark.parametrize(
    ("caso", "borrador"),
    [
        # Notación española + redondeo: lo que v1.2 explicitó como fiel.
        (
            "notacion_espanola",
            "El S&P 500 cierra en 5.100 puntos tras subir un 2,5% semanal",
        ),
        # Dirección sin repetir el número.
        ("solo_direccion", "El S&P 500 cierra al alza y el Nasdaq retrocede"),
        # Omitir datos es legítimo: el titular no tiene sitio para todos.
        ("subconjunto", "El IPC interanual se sitúa en el 3,1%"),
    ],
)
def test_approves_faithful_drafts(validator_agent, caso, borrador):
    """La otra dirección del fallo: rechazar de más también rompe el producto.

    Un rechazo descarta el titular y publica el texto genérico. Si el validador
    se volviera quisquilloso con los redondeos o la notación española, el
    pipeline publicaría el genérico todas las semanas y los logs dirían que
    todo funciona.
    """
    result = validator_agent.review_draft(borrador, SOURCE_DATA)

    assert_veredicto_real(result)
    assert result["approved"] is True, (
        f"El validador rechazó un borrador fiel ({caso}): {borrador!r}. "
        f"Motivo que dio: {result['reason']!r}"
    )


def test_rejects_investment_advice(validator_agent):
    """El otro criterio de rechazo del prompt: nada publicable como consejo.

    Las cifras son correctas; lo que sobra es el imperativo. Va en el nightly
    porque es una obligación regulatoria, no una preferencia de estilo.
    """
    result = validator_agent.review_draft(
        "Momento de comprar: el S&P 500 sube un 2,5%", SOURCE_DATA
    )

    assert_veredicto_real(result)
    assert result["approved"] is False, (
        f"El validador aprobó una recomendación de inversión: {result['reason']!r}"
    )


def test_generate_headline_reaches_the_model(llm_client):
    """`generate_headline` devuelve un titular del modelo, no su fallback.

    El `except Exception` del cliente convierte cualquier fallo —modelo
    retirado, parámetro rechazado, red caída— en un string plausible. Sin este
    test, el pipeline publicaría "Cierre Semanal: Resumen del Mercado" para
    siempre y ningún gate lo notaría.
    """
    headline = llm_client.generate_headline(SOURCE_DATA)

    assert headline != FALLBACK_HEADLINE, (
        "La llamada a Anthropic falló y devolvió el titular de emergencia: "
        "revisar el id del modelo y los parámetros de `messages.create()`."
    )
    assert headline.strip()
    # Contrato de formato con los canales (ADR-003): una línea, texto plano.
    # Los asteriscos los limpia el cliente; que reaparezcan significa un
    # envoltorio nuevo que la limpieza no cubre.
    assert "\n" not in headline
    assert "*" not in headline
    assert not headline.startswith('"')
    # El límite de producto son 120 caracteres y el cliente solo lo loggea.
    # Aquí se comprueba un techo más alto: un titular de 90 y uno de 130 son
    # el mismo estado del mundo, pero uno de 400 significa que el modelo dejó
    # de entender "una sola línea" y el post sale roto.
    assert len(headline) <= 250, f"Titular de {len(headline)} caracteres."


@pytest.mark.parametrize(
    "source",
    [SOURCE_DATA, SOURCE_DATA_OTRA_SEMANA],
    ids=["semana_base", "otra_semana"],
)
def test_generated_headline_survives_its_own_validator(
    llm_client, validator_agent, source
):
    """El circuito completo de ADR-001 con las dos llamadas reales encadenadas.

    Es lo que corre en producción cada viernes: generar y revisar. Si los dos
    prompts se desalinean —uno pide cifras exactas, el otro las rechaza— el
    pipeline publica el genérico sin fallar en ningún sitio. Ningún test con
    mocks puede ver ese desacuerdo, porque cada mock devuelve lo que le
    pusimos.
    """
    headline = llm_client.generate_headline(source)
    assert headline != FALLBACK_HEADLINE, (
        "La generación falló; el circuito no se puede verificar."
    )

    result = validator_agent.review_draft(headline, source)

    assert_veredicto_real(result)
    assert result["approved"] is True, (
        f"El validador rechazó el titular que generó el propio pipeline. "
        f"Titular: {headline!r}. Motivo: {result['reason']!r}"
    )
