"""Decide si hoy toca avisar del vencimiento del token de LinkedIn.

El token dura ~60 días y se reemite a mano desde el token generator del portal:
rotar es coste externo y lo máximo que el repo puede hacer es avisar a tiempo.
El aviso ya existía en `scripts/check_publishers.py`, pero es un `print` de un
script que solo corre a mano, así que se disparaba únicamente si alguien lo
ejecutaba dentro de la ventana. Esto es la mitad decidible, separada del envío
para poder testearla sin red.

    python scripts/linkedin_token_alert.py

Imprime el mensaje si toca avisar y no imprime nada si no toca. Sale con 0
siempre que la decisión se haya podido tomar; un crash sale distinto de 0 a
propósito, porque el paso del workflow corre con `set -e` y así un fallo pone
el job en rojo en vez de pasar por silencio.

Solo stdlib, y no importa `macro_pipeline` a propósito: el paso corre antes de
`setup-python` y de `pip install`, para que la alarma no dependa de que el
install funcione.
"""

from __future__ import annotations

import os
import sys
from datetime import date

VIDA_UTIL_DIAS = 60

# Tres pulsos espaciados antes de vencer, y después diario. Avisar los diez
# días seguidos es como se entrena a ignorar un canal — y por éste llegan las
# alertas de degradación, donde por convención del repo un mensaje significa
# que algo se rompió.
PULSOS = (50, 55, 58)

BANDERA = "PUBLISH_LINKEDIN"
EMITIDO = "LINKEDIN_TOKEN_ISSUED"

_COMO_ROTAR = (
    "Reemitirlo desde el token generator del portal y actualizar "
    f"{EMITIDO} en el .env y en las variables del repo."
)


def mensaje_de_aviso(hoy: date, bandera: str, emitido: str) -> str | None:
    """El texto a mandar, o `None` si hoy no toca avisar.

    Las dos guardas van en este orden y no es estético. Con LinkedIn apagado y
    la fecha ilegible el resultado tiene que ser silencio: si se miraran al
    revés, apagar la red dejaría de silenciar justo cuando la fecha quedó sin
    mantener —el caso más probable después de un apagado largo— y el aviso
    volvería a sonar todas las semanas por algo ya decidido.

    Solo el `false` exacto silencia. Un valor que no se entiende cae del lado
    ruidoso, igual que una bandera ausente: es la misma trampa que el orden de
    las dos primeras ramas de `_startup_exit_code`, donde un switch ilegible
    quedaba idéntico a un apagado deliberado.
    """
    if bandera.strip().lower() == "false":
        return None

    crudo = emitido.strip()
    if not crudo:
        return (
            f"[LinkedIn] No puedo avisar del vencimiento del token: falta "
            f"{EMITIDO}. La alarma esta desarmada hasta que se cargue."
        )

    try:
        dia = date.fromisoformat(crudo)
    except ValueError:
        return (
            f"[LinkedIn] No puedo avisar del vencimiento del token: {EMITIDO} "
            f"no es una fecha ISO. La alarma esta desarmada hasta que se "
            f"corrija."
        )

    edad = (hoy - dia).days
    if edad < 0:
        return (
            f"[LinkedIn] {EMITIDO} esta en el futuro, asi que no puedo "
            f"calcular el vencimiento. La alarma esta desarmada hasta que se "
            f"corrija."
        )
    if edad >= VIDA_UTIL_DIAS:
        return (
            f"[LinkedIn] El token esta vencido: emitido hace {edad} dias "
            f"(dura ~{VIDA_UTIL_DIAS}). {_COMO_ROTAR}"
        )
    if edad in PULSOS:
        return (
            f"[LinkedIn] Al token le quedan ~{VIDA_UTIL_DIAS - edad} dias "
            f"(emitido hace {edad}). {_COMO_ROTAR}"
        )
    return None


def main() -> int:
    mensaje = mensaje_de_aviso(
        date.today(),
        os.environ.get(BANDERA, ""),
        os.environ.get(EMITIDO, ""),
    )
    if mensaje:
        print(mensaje)
    return 0


if __name__ == "__main__":
    sys.exit(main())
