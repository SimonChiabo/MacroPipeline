"""Banderas de encendido/apagado por red de publicacion.

Vive aparte de `orchestration/main.py` porque lo usan dos consumidores que no
se importan entre si: el orquestador y `scripts/check_publishers.py`. El script
no puede importar el orquestador sin arrastrar pandas, opentelemetry y los
siete clientes.
"""

import os

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
