"""Verifica las credenciales de X y LinkedIn sin publicar nada.

El pipeline solo comprueba que las variables *existan*: con los placeholders de
`.env.example` cargados, `publishers_ready` puede quedar en True y el fallo
aparecer recién después de que un humano aprobó el post en Telegram. Este
script hace la pregunta que importa —¿estas credenciales sirven para publicar?—
contra endpoints de solo lectura.

    python scripts/check_publishers.py

Sale con código 1 si algo no está listo. No imprime credenciales.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

X_VARS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
LINKEDIN_VARS = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN")

# Solo ASCII en la salida: la consola de Windows usa cp1252 y los
# caracteres de dibujo de caja revientan con UnicodeEncodeError.
OK, FAIL, WARN = "[ OK ]", "[FALLA]", "[AVISO]"


def _is_placeholder(value: str) -> bool:
    """Los valores de `.env.example` cuentan como 'sin cargar'."""
    return value.startswith("your_") or value.endswith("tu_id_de_linkedin")


def _check_present(names: tuple[str, ...]) -> list[str]:
    """Devuelve los nombres que faltan o siguen con el placeholder."""
    missing = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            print(f"{FAIL} {name}: sin definir")
            missing.append(name)
        elif _is_placeholder(value):
            print(f"{FAIL} {name}: sigue con el placeholder de .env.example")
            missing.append(name)
        else:
            print(f"{OK} {name}: cargada ({len(value)} caracteres)")
    return missing


def check_x() -> bool:
    """GET /2/users/me: confirma que las cuatro credenciales autentican."""
    print("\n-- X ----------------------------------------------")
    if _check_present(X_VARS):
        return False

    session = OAuth1Session(
        os.environ["X_API_KEY"],
        client_secret=os.environ["X_API_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_SECRET"],
    )
    try:
        response = session.get("https://api.twitter.com/2/users/me", timeout=15)
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de X: {e}")
        return False

    if response.status_code == 401:
        print(f"{FAIL} 401: las credenciales no autentican. Revisar que las cuatro")
        print("       sean de la misma app y estén bien copiadas.")
        return False
    if response.status_code != 200:
        print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
        return False

    user = response.json().get("data", {})
    print(f"{OK} Autenticado como @{user.get('username')} ({user.get('name')})")

    # X informa el permiso del token en una cabecera de cualquier respuesta.
    # Es la unica forma de confirmar que se puede publicar sin publicar.
    access_level = response.headers.get("x-access-level")
    if access_level == "read":
        print(f"{FAIL} x-access-level: read. El token es de SOLO LECTURA y")
        print("       publicar va a dar 403. El permiso queda grabado en el")
        print("       token al emitirlo, asi que no alcanza con cambiarlo en")
        print("       la app: hay que poner la app en 'Read and write' y")
        print("       DESPUES regenerar Access Token y Secret (los dos")
        print("       primeros valores, API Key y Secret, no cambian).")
        return False
    if access_level and "write" in access_level:
        print(f"{OK} x-access-level: {access_level}. El token puede publicar.")
        return True

    print(f"{WARN} X no informo x-access-level: no se pudo confirmar el")
    print("       permiso de escritura sin publicar.")
    return True


def check_linkedin() -> bool:
    """GET /v2/userinfo: confirma el token y muestra el PERSON_URN correcto."""
    print("\n-- LinkedIn ---------------------------------------")
    missing = _check_present(LINKEDIN_VARS)

    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    if not token or _is_placeholder(token):
        return False

    try:
        response = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de LinkedIn: {e}")
        return False

    if response.status_code == 401:
        print(f"{FAIL} 401: el token es inválido o expiró (duran ~60 días).")
        return False
    if response.status_code == 403:
        print(f"{WARN} 403 en /v2/userinfo: el token no tiene los scopes")
        print("       'openid profile'. No impide publicar si tiene")
        print("       w_member_social, pero hay que poner el PERSON_URN a mano.")
        return not missing
    if response.status_code != 200:
        print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
        return False

    info = response.json()
    urn = f"urn:li:person:{info.get('sub')}"
    print(f"{OK} Token válido para {info.get('name')}")
    print(f"{OK} Este es el valor que va en .env:")
    print(f"       LINKEDIN_PERSON_URN={urn}")

    configured = os.environ.get("LINKEDIN_PERSON_URN", "").strip()
    if configured and not _is_placeholder(configured) and configured != urn:
        print(f"{FAIL} El LINKEDIN_PERSON_URN cargado no coincide con el del token.")
        return False

    issued = os.environ.get("LINKEDIN_TOKEN_ISSUED", "").strip()
    if issued:
        try:
            age = (date.today() - date.fromisoformat(issued)).days
            marker = WARN if age > 50 else OK
            print(f"{marker} Token emitido hace {age} días (expira a los ~60).")
        except ValueError:
            print(f"{WARN} LINKEDIN_TOKEN_ISSUED no es una fecha ISO válida.")
    else:
        print(f"{WARN} Sin LINKEDIN_TOKEN_ISSUED: no se puede avisar del vencimiento.")

    return not missing


def main() -> int:
    print("Verificación de credenciales de publicación (no publica nada).")
    x_ok = check_x()
    linkedin_ok = check_linkedin()

    print("\n-- Resultado --------------------------------------")
    print(f"X:        {'listo' if x_ok else 'NO listo'}")
    print(f"LinkedIn: {'listo' if linkedin_ok else 'NO listo'}")
    if x_ok and linkedin_ok:
        print("\npublishers_ready va a quedar en True y la publicación real")
        print("puede ejercitarse de punta a punta.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
