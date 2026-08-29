"""Switches de encendido/apagado por componente.

Vive en la raiz del paquete y no bajo `publishers/` por dos motivos: lo usan
dos consumidores que no se importan entre si —el orquestador y
`scripts/check_publishers.py`, que no puede arrastrar pandas y los siete
clientes—, y desde ADR-009 cubre los ocho componentes con credenciales y no
solo las dos redes.
"""

import os
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

USE_FMP_VAR = "USE_FMP"
USE_AV_VAR = "USE_AV"
USE_FRED_VAR = "USE_FRED"
USE_ANTHROPIC_VAR = "USE_ANTHROPIC"
USE_R2_VAR = "USE_R2"
USE_TELEGRAM_VAR = "USE_TELEGRAM"
PUBLISH_X_VAR = "PUBLISH_X"
PUBLISH_LINKEDIN_VAR = "PUBLISH_LINKEDIN"


def component_enabled(var: str) -> bool:
    """`True`/`False` desde una variable de entorno, o levanta.

    Ausente o vacia -> True: el default es participar, y eso es lo que hace que
    un `.env` sin copiar deje todo encendido y por lo tanto todo alertando, en
    vez de callarse.

    Cualquier valor que no sea `true` o `false` levanta `ValueError` a
    proposito, mostrando lo que el operador realmente tipeo. Las dos formas de
    adivinar son silenciosas y las dos hacen dano.
    """
    original = os.environ.get(var, "")
    raw = original.strip().lower()
    if not raw:
        return True
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(
        f"{var}={original!r} no es un valor valido: se espera 'true' o 'false'."
    )


def read_switch(var: str) -> tuple[bool, str | None]:
    """`(encendido, motivo)`: la version que no levanta.

    Un motivo distinto de `None` significa que **no se pudo leer la intencion
    del operador**, que no es lo mismo que una credencial ausente: degradar
    seria adivinar. Devuelve `False` en ese caso, asi que el componente no se
    construye y queda indistinguible de un apagado deliberado — por eso el
    motivo es lo unico que los separa, y por eso el punto de decision mira los
    `switch_errors` antes que ninguna rama de apagado.

    `component_enabled` sigue existiendo y levantando porque
    `scripts/check_publishers.py` lo quiere asi: ahi un valor invalido debe
    romper el chequeo.
    """
    try:
        return component_enabled(var), None
    except ValueError as e:
        return False, str(e)


def build_component[T](
    name: str, factory: Callable[[], T], enabled: bool
) -> tuple[T | None, str | None]:
    """Construye un componente, devolviendo (cliente, motivo).

    Las tres combinaciones son distintas y el orquestador las distingue:

    - `(cliente, None)` — listo.
    - `(None, None)` — apagado a proposito: se loggea y nada mas. **No alerta**:
      una decision tuya no es un fallo, y alertar cada semana por una pausa
      deliberada es lo que hace que se dejen de leer las alertas.
    - `(None, motivo)` — encendido y roto: falta alguna credencial. El motivo es
      el texto del `ValueError` del cliente y termina en la alerta de Telegram.

    Solo se atrapa `ValueError`, que es lo que levantan los clientes cuando
    falta una credencial. Cualquier otra excepcion sale y mata la run: no es un
    componente roto, es un bug.
    """
    if not enabled:
        logger.info("component_disabled", component=name)
        return None, None
    try:
        return factory(), None
    except ValueError as e:
        logger.warning("component_not_configured", component=name, reason=str(e))
        return None, str(e)
