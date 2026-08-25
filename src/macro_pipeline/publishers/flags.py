"""Banderas de encendido/apagado por red de publicacion.

Vive aparte de `orchestration/main.py` porque lo usan dos consumidores que no
se importan entre si: el orquestador y `scripts/check_publishers.py`. El script
no puede importar el orquestador sin arrastrar pandas, opentelemetry y los
siete clientes.
"""

import os
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

PUBLISH_X_VAR = "PUBLISH_X"
PUBLISH_LINKEDIN_VAR = "PUBLISH_LINKEDIN"


def publisher_enabled(var: str) -> bool:
    """`True`/`False` desde una variable de entorno, o levanta.

    Ausente o vacia -> True: el default es publicar, que es lo que hacia el
    pipeline antes de que estas banderas existieran.

    Cualquier valor que no sea `true` o `false` levanta `ValueError` a
    proposito, mostrando lo que el operador realmente tipeo. Ver el docstring
    de `tests/unit/test_publisher_flags.py`: las dos formas de adivinar son
    silenciosas y las dos hacen dano.
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


def build_publisher[T](
    name: str, factory: Callable[[], T], enabled: bool
) -> tuple[T | None, str | None]:
    """Construye un cliente de publicacion, devolviendo (cliente, motivo).

    Las tres combinaciones son distintas y el orquestador las distingue:

    - `(cliente, None)` — listo.
    - `(None, None)` — apagado a proposito: se loggea y nada mas. **No alerta**:
      una decision tuya no es un fallo, y alertar cada semana por una pausa
      deliberada es lo que hace que se dejen de leer las alertas.
    - `(None, motivo)` — roto: falta alguna credencial. El motivo es el texto
      del `ValueError` del cliente y termina en la alerta de Telegram.

    Solo se atrapa `ValueError`, que es lo que levantan `XClient` y
    `LinkedInClient` cuando falta una credencial. Cualquier otra excepcion sale
    y mata la run: no es una red rota, es un bug.
    """
    if not enabled:
        logger.info("publisher_disabled", publisher=name)
        return None, None
    try:
        return factory(), None
    except ValueError as e:
        logger.warning("publisher_not_configured", publisher=name, reason=str(e))
        return None, str(e)
