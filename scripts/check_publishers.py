"""Verifica las credenciales de X y LinkedIn sin publicar nada.

El pipeline solo comprueba que las variables *existan*: con los placeholders de
`.env.example` cargados una red queda marcada como lista y el fallo aparece
recién después de que un humano aprobó el post en Telegram. Este script hace la
pregunta que importa —¿estas credenciales sirven para publicar?— contra
endpoints de solo lectura. Una red apagada con `PUBLISH_X=false` o
`PUBLISH_LINKEDIN=false` no se verifica y no afecta el código de salida.

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

from macro_pipeline.publishers.flags import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    publisher_enabled,
)

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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Nombre -> valor de un fichero .env. Ignora comentarios y líneas vacías.

    `partition` y no `split`: hay valores que llevan `=` dentro
    (`OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer ...`).
    """
    pairs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs[name.strip()] = value.strip()
    return pairs


def _commented_names(path: Path) -> set[str]:
    """Nombres declarados pero comentados (`# VAR=ejemplo`): opcionales."""
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        candidato = line.lstrip("#").strip()
        name, sep, _ = candidato.partition("=")
        name = name.strip()
        if sep and name.replace("_", "").isalnum() and name.isupper():
            names.add(name)
    return names


def compare_env_files(example_path: Path, env_path: Path) -> list[tuple[str, str]]:
    """Deriva entre `.env.example` y `.env`, como (variable, motivo).

    Existe por `TELEGRAM_ALLOWED_USER_ID`: declarada en el ejemplo y marcada
    como CRÍTICA, implementada en el código, y ausente del `.env` real durante
    meses. Sin ella el HITL de ADR-004 acepta el botón de cualquiera en el
    chat, y lo único que lo decía era un `logger.warning` al arrancar.

    Se miran las dos direcciones. Una variable que está en el `.env` y no en
    el ejemplo no rompe nada hoy, pero es como se fabrica el problema para el
    siguiente que clone el repo: copia el ejemplo y le falta.
    """
    ejemplo = _parse_env_file(example_path)
    real = _parse_env_file(env_path)
    # El ejemplo documenta las opcionales comentandolas con un valor de
    # muestra (`# LINKEDIN_TOKEN_ISSUED=2026-05-15`). Esas cuentan como
    # documentadas pero no se exigen: no estar en el `.env` es lo normal.
    documentadas = ejemplo.keys() | _commented_names(example_path)

    hallazgos: list[tuple[str, str]] = []
    for name in ejemplo:
        if name not in real:
            hallazgos.append((name, "ausente"))
        elif _is_placeholder(real[name]):
            hallazgos.append((name, "placeholder"))
    hallazgos.extend(
        (name, "sin documentar") for name in real if name not in documentadas
    )
    return hallazgos


def report_env_drift(example_path: Path, env_path: Path) -> None:
    """Imprime la deriva entre los dos ficheros. No decide el código de salida.

    El código de salida del script significa "las credenciales de publicación
    sirven". Una variable opcional sin poner en el `.env` no es eso, y un
    chequeo que pone el script en rojo por una decisión pendiente termina
    desactivado. Se informa y decide quien lee.
    """
    print("\n-- .env vs .env.example ---------------------------")
    if not env_path.exists():
        print(f"{WARN} No hay .env local: nada que comparar.")
        return

    hallazgos = compare_env_files(example_path, env_path)
    if not hallazgos:
        print(f"{OK} Sin deriva entre .env y .env.example.")
        return

    explicacion = {
        "ausente": "declarada en .env.example y sin poner en .env",
        "placeholder": "sigue con el valor de ejemplo",
        "sin documentar": "esta en .env pero .env.example no la menciona",
    }
    for name, motivo in hallazgos:
        print(f"{WARN} {name}: {motivo} ({explicacion[motivo]})")


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
    report_env_drift(ROOT / ".env.example", ROOT / ".env")

    try:
        x_on = publisher_enabled(PUBLISH_X_VAR)
        linkedin_on = publisher_enabled(PUBLISH_LINKEDIN_VAR)
    except ValueError as e:
        # El unico sitio donde el valor malo no sale por traceback: este script
        # existe para decirle a un humano que tiene mal configurado, y el
        # orquestador ya se muere solo si la bandera no se entiende.
        print(f"{FAIL} {e}")
        return 1

    # Una red apagada no se chequea y no cuenta para el código de salida: no
    # tiene credenciales que sirvan ni que dejen de servir, porque no publica.
    if x_on:
        x_ok = check_x()
    else:
        print("\n-- X ----------------------------------------------")
        print(f"{OK} Apagada por {PUBLISH_X_VAR}=false: no se verifica.")
        x_ok = True

    if linkedin_on:
        linkedin_ok = check_linkedin()
    else:
        print("\n-- LinkedIn ---------------------------------------")
        print(f"{OK} Apagada por {PUBLISH_LINKEDIN_VAR}=false: no se verifica.")
        linkedin_ok = True

    print("\n-- Resultado --------------------------------------")
    print(f"X:        {'apagada' if not x_on else 'listo' if x_ok else 'NO listo'}")
    print(
        f"LinkedIn: "
        f"{'apagada' if not linkedin_on else 'listo' if linkedin_ok else 'NO listo'}"
    )
    if not x_on and not linkedin_on:
        print("\nLas dos redes están apagadas: el pipeline no va a publicar")
        print("en ninguna parte y aborta antes de tocar el estado.")
        return 0
    if x_ok and linkedin_ok:
        print("\nLa publicación real puede ejercitarse de punta a punta en las")
        print("redes encendidas.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
