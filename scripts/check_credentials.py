"""Verifica que las credenciales de los seis componentes sirvan de verdad.

El pipeline solo comprueba que las variables *existan*: una key presente pero
rotada o revocada es indistinguible de una buena hasta que la corrida pega
contra la API. Este script hace la pregunta que importa —¿esta credencial
sirve?— contra endpoints baratos. Un componente apagado con su `USE_*` o
`PUBLISH_*` en `false` no se verifica y no afecta el código de salida.

    python scripts/check_credentials.py

**No publica nada, pero ya no es del todo pasivo.** Para R2 escribe y borra un
objeto de prueba bajo `healthcheck/` en tu propio bucket: los tokens de R2 se
emiten de solo lectura o de lectura y escritura, y la API de S3 no tiene nada
como la cabecera `x-access-level` de X. Confirmar que el token puede guardar el
estado exige guardarlo una vez. Nada de esto toca `state/state.db`.

Sale con código 1 si algo encendido no está listo. No imprime credenciales.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session

from macro_pipeline.components import (
    PUBLISH_LINKEDIN_VAR,
    PUBLISH_X_VAR,
    USE_ANTHROPIC_VAR,
    USE_AV_VAR,
    USE_FRED_VAR,
    USE_R2_VAR,
    component_enabled,
)
from macro_pipeline.storage.r2_client import R2Client, R2ClientError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

X_VARS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
LINKEDIN_VARS = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN")

# Solo ASCII en la salida: la consola de Windows usa cp1252 y los
# caracteres de dibujo de caja revientan con UnicodeEncodeError.
OK, FAIL, WARN = "[ OK ]", "[FALLA]", "[AVISO]"

# Tres veredictos y no dos: el rate limit de Alpha Vantage no es "listo" ni
# "NO listo". Decir "listo" de algo que no se pudo verificar es exactamente el
# verde que no verifico nada, y decir "NO listo" de una cuota agotada pone el
# script en rojo por usarlo.
LISTO, NO_LISTO, SIN_VERIFICAR = "listo", "NO listo", "sin verificar"

FRED_VARS = ("FRED_API_KEY",)
AV_VARS = ("ALPHA_VANTAGE_API_KEY",)
ANTHROPIC_VARS = ("ANTHROPIC_API_KEY",)
R2_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

# El marcador que ya conoce el repo. Mismo texto y mismo motivo que en
# `tests/contract/test_av_contract.py`: separa "no pudimos verificar" de "la
# credencial no sirve". No colisiona con el del nightly, que se grepea sobre
# `pytest-output.txt`.
AV_RATE_LIMIT_MARKER = "AV_RATE_LIMIT"


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

    El código de salida del script significa "algún componente encendido tiene
    credenciales que no sirven". Una variable opcional sin poner en el `.env`
    no es eso, y un chequeo que pone el script en rojo por una decisión
    pendiente termina desactivado. Se informa y decide quien lee.
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


def _encabezado(titulo: str) -> None:
    """La linea de seccion, del mismo ancho para los seis."""
    print(f"\n-- {titulo} " + "-" * max(3, 48 - len(titulo)))


def _veredicto(chequeo: Callable[[], bool]) -> Callable[[], str]:
    """Adapta los dos chequeos que ya devolvian `bool` a los tres veredictos.

    X y LinkedIn no tienen un caso "sin verificar": un corte de red ya sale
    como `False` y con su marcador. Envolverlos aca evita tocarlos y evita
    romper los tests que asertan `check_linkedin() is False`.
    """
    return lambda: LISTO if chequeo() else NO_LISTO


def _mensaje_de_error(response: requests.Response) -> str:
    """El texto que la API da para explicarse, o el cuerpo crudo.

    FRED pone el motivo real en `error_message` y devuelve 400, no 401: sin
    esto el diagnostico seria "HTTP 400" a secas.
    """
    try:
        cuerpo = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(cuerpo, dict) and "error_message" in cuerpo:
        return str(cuerpo["error_message"])
    return response.text[:200]


def check_x() -> bool:
    """GET /2/users/me: confirma que las cuatro credenciales autentican.

    El encabezado de seccion lo imprime `main()` para los seis componentes:
    imprimirlo tambien aca lo duplicaba.
    """
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


def _avisar_vencimiento() -> None:
    """Imprime la edad del token. Corre pase lo que pase con la API.

    Estaba al final de `check_linkedin()`, después de tres `return` tempranos,
    así que un 403 por scopes —o la API caída, o un PERSON_URN que no coincide—
    se comía el aviso de vencimiento incluso corriendo el script a mano. La edad
    se calcula contra una fecha local: no necesita que LinkedIn conteste.
    """
    issued = os.environ.get("LINKEDIN_TOKEN_ISSUED", "").strip()
    if not issued:
        print(f"{WARN} Sin LINKEDIN_TOKEN_ISSUED: no se puede avisar del vencimiento.")
        return
    try:
        age = (date.today() - date.fromisoformat(issued)).days
    except ValueError:
        print(f"{WARN} LINKEDIN_TOKEN_ISSUED no es una fecha ISO válida.")
        return
    marker = WARN if age > 50 else OK
    print(f"{marker} Token emitido hace {age} días (expira a los ~60).")


def check_linkedin() -> bool:
    """GET /v2/userinfo: confirma el token y muestra el PERSON_URN correcto.

    El encabezado de seccion lo imprime `main()`, igual que para X.
    """
    missing = _check_present(LINKEDIN_VARS)

    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    if not token or _is_placeholder(token):
        return False

    _avisar_vencimiento()

    try:
        response = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de LinkedIn: {e}")
        print("LINKEDIN_UNREACHABLE")
        return False

    if response.status_code == 401:
        print(f"{FAIL} 401: el token es inválido o expiró (duran ~60 días).")
        print("LINKEDIN_TOKEN_DEAD")
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

    return not missing


def check_fred() -> str:
    """GET /fred/series: la llamada mas barata que igual autentica."""
    if _check_present(FRED_VARS):
        return NO_LISTO

    try:
        response = requests.get(
            "https://api.stlouisfed.org/fred/series",
            params={
                "series_id": "UNRATE",
                "file_type": "json",
                "api_key": os.environ["FRED_API_KEY"],
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de FRED: {e}")
        print("       La key quedo sin verificar; no es necesariamente ella.")
        return NO_LISTO

    if response.status_code == 200:
        print(f"{OK} La key de FRED autentica.")
        return LISTO

    print(f"{FAIL} HTTP {response.status_code}: {_mensaje_de_error(response)}")
    return NO_LISTO


def check_av() -> str:
    """GLOBAL_QUOTE: una llamada, la mas barata, y el cuerpo manda."""
    if _check_present(AV_VARS):
        return NO_LISTO

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": "SPY",
                "apikey": os.environ["ALPHA_VANTAGE_API_KEY"],
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"{FAIL} No se pudo contactar la API de Alpha Vantage: {e}")
        return NO_LISTO

    if response.status_code != 200:
        print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
        return NO_LISTO

    try:
        datos = response.json()
    except ValueError:
        print(f"{FAIL} Alpha Vantage no devolvió JSON: {response.text[:200]}")
        return NO_LISTO

    if "Error Message" in datos:
        print(f"{FAIL} {datos['Error Message']}")
        return NO_LISTO

    if "rate limit" in str(datos.get("Information", "")).lower():
        print(f"{WARN} {AV_RATE_LIMIT_MARKER}: se agotó la cuota diaria, así que")
        print("       la key quedó SIN VERIFICAR. No es un fallo de la")
        print("       credencial: este mismo chequeo consume una llamada de esa")
        print("       cuota cada vez que corre.")
        return SIN_VERIFICAR

    print(f"{OK} La key de Alpha Vantage autentica.")
    return LISTO


# Tope de páginas. El listado es chico, pero un `has_more` siempre en `true`
# —un bug de la API, un proxy raro— dejaría el chequeo colgado para siempre.
_MAX_PAGINAS = 10


def _esta_listado(modelo: str, ids: list[str]) -> bool:
    """El modelo del pipeline, buscado como alias o como snapshot fechado.

    Comprobado en vivo el 2026-09-01: `/v1/models` devuelve
    `claude-haiku-4-5-20251001` y NO el alias `claude-haiku-4-5`, que es el que
    usa el pipeline y funciona perfectamente. Con la igualdad a secas, la
    primera corrida real avisó de un retiro inexistente. Es el mismo modo de
    fallo que mirar solo la primera página: un AVISO falso entrena a ignorar el
    verdadero, y ese es justo el aviso que este chequeo existe para dar.

    El sufijo tiene que ser una fecha de ocho dígitos y nada más. Un
    `startswith` a secas daría por presente cualquier id que empiece igual, y
    aflojar el match hasta volverlo mudo sería peor que el falso aviso.
    """
    fechado = re.compile(rf"^{re.escape(modelo)}-\d{{8}}$")
    return any(i == modelo or fechado.match(i) for i in ids)


def check_anthropic() -> str:
    """GET /v1/models: autentica sin gastar tokens, por eso este y no un mensaje."""
    # Adentro a propósito: importar esto arrastra el SDK de Anthropic entero
    # (1642 módulos, ~1.9 s medidos) y es el import más caro del script. Acá
    # solo lo paga quien tiene Anthropic encendido; el paso del nightly, que lo
    # apaga, no lo paga nunca. Se importa la constante y no se copia el string:
    # una copia mentiría el día que el pipeline cambie de modelo.
    from macro_pipeline.llm.client import MODEL

    if _check_present(ANTHROPIC_VARS):
        return NO_LISTO

    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
    }
    params: dict[str, str | int] = {"limit": 1000}
    ids: list[str] = []

    for _ in range(_MAX_PAGINAS):
        try:
            response = requests.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                params=params,
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"{FAIL} No se pudo contactar la API de Anthropic: {e}")
            return NO_LISTO

        if response.status_code == 401:
            print(f"{FAIL} 401: la ANTHROPIC_API_KEY no autentica.")
            return NO_LISTO
        if response.status_code != 200:
            print(f"{FAIL} HTTP {response.status_code}: {response.text[:200]}")
            return NO_LISTO

        cuerpo = response.json()
        ids.extend(str(m.get("id")) for m in cuerpo.get("data", []))
        if not cuerpo.get("has_more"):
            break
        params = {"limit": 1000, "after_id": ids[-1]}

    print(f"{OK} La key de Anthropic autentica ({len(ids)} modelos visibles).")

    if not _esta_listado(MODEL, ids):
        print(f"{WARN} {MODEL} no aparece en el listado: puede estar retirado o")
        print("       no habilitado para esta key. No impide publicar hoy —el")
        print("       pipeline caería al titular de emergencia— pero es la")
        print("       señal temprana, y este repo ya se comió un retiro.")

    return LISTO


# Prefijo propio, lejos de `state/`. La sonda no puede acercarse al fichero
# cuya perdida republica un cierre.
HEALTHCHECK_PREFIX = "healthcheck/"


def check_r2() -> str:
    """Sonda de escritura: es lo unico que no se puede obtener leyendo.

    Los tokens de R2 se emiten *Object Read only* u *Object Read & Write*, y la
    API de S3 no tiene equivalente a la cabecera `x-access-level` que salva a X:
    no hay dry-run ni forma de confirmar `PutObject` sin poner un objeto.
    """
    if _check_present(R2_VARS):
        return NO_LISTO

    try:
        cliente = R2Client()
    except ValueError as e:
        print(f"{FAIL} {e}")
        return NO_LISTO

    key = f"{HEALTHCHECK_PREFIX}check-credentials-{uuid.uuid4()}.txt"
    cuerpo = f"macropipeline healthcheck {datetime.now(UTC).isoformat()}".encode()

    # El put va PRIMERO a proposito. `download_object` traduce `NoSuchBucket` a
    # ausencia (None) —correcto para el sincronizado de estado, engañoso aca—,
    # asi que arrancando por la lectura un bucket inexistente se leeria como
    # "primera corrida" y esto pasaria en verde.
    #
    # **Verificado en vivo el 2026-09-01 contra el bucket real**: put, get y
    # delete bajo `healthcheck/` funcionan con el token actual, y el prefijo
    # quedó vacío después.
    try:
        cliente.upload_object(key, cuerpo, "text/plain")
    except R2ClientError as e:
        print(f"{FAIL} No se pudo escribir en R2: {e}")
        if "AccessDenied" in str(e):
            # "SOLO LECTURA" va entero en una linea a proposito: el test lo
            # busca como substring, y partido en dos —que es como quedaba al
            # justificar el parrafo— no lo encuentra nadie que grepee la salida.
            print("       Es el mismo caso que el token de X: este es de")
            print("       SOLO LECTURA. Hay que reemitirlo con permiso 'Object")
            print("       Read & Write'; cambiarlo en el panel no alcanza para")
            print("       un token ya emitido.")
        return NO_LISTO
    print(f"{OK} PutObject: el token puede escribir.")

    try:
        leido = cliente.download_object(key)
    except R2ClientError as e:
        print(f"{FAIL} Se escribió pero no se pudo releer: {e}")
        return NO_LISTO
    if leido != cuerpo:
        print(f"{FAIL} Se escribió pero no se pudo releer igual: lo que volvió")
        print("       no coincide con lo que se subió.")
        return NO_LISTO
    print(f"{OK} GetObject: lo escrito se lee igual.")

    try:
        cliente.delete_object(key)
    except R2ClientError as e:
        print(f"{WARN} No se pudo borrar el objeto de prueba: {e}")
        print(f"{WARN} Queda huérfano en {key}. No impide publicar: el pipeline")
        print("       nunca borra, así que el permiso de Delete no le hace falta.")
        return LISTO
    print(f"{OK} DeleteObject: el objeto de prueba se limpió.")

    return LISTO


def main() -> int:
    print("Verificación de credenciales. No publica nada; para R2 escribe y")
    print("borra un objeto de prueba en tu propio bucket (ver el docstring).")
    report_env_drift(ROOT / ".env.example", ROOT / ".env")

    # Las funciones se nombran acá y no en una constante de módulo: así
    # `monkeypatch.setattr` sobre el módulo sigue funcionando en los tests.
    chequeos: list[tuple[str, str, Callable[[], str]]] = [
        ("X", PUBLISH_X_VAR, _veredicto(check_x)),
        ("LinkedIn", PUBLISH_LINKEDIN_VAR, _veredicto(check_linkedin)),
        ("FRED", USE_FRED_VAR, check_fred),
        ("Alpha Vantage", USE_AV_VAR, check_av),
        ("Anthropic", USE_ANTHROPIC_VAR, check_anthropic),
        ("R2", USE_R2_VAR, check_r2),
    ]

    # Todos los switches, antes de contactar a nadie. Uno ilegible no puede
    # dejar media verificación hecha.
    estados: list[tuple[str, str, Callable[[], str], bool]] = []
    for titulo, var, chequeo in chequeos:
        try:
            estados.append((titulo, var, chequeo, component_enabled(var)))
        except ValueError as e:
            # El único sitio donde el valor malo no sale por traceback: este
            # script existe para decirle a un humano qué tiene mal configurado.
            print(f"{FAIL} {e}")
            return 1

    resultados: list[tuple[str, str]] = []
    for titulo, var, chequeo, encendido in estados:
        _encabezado(titulo)
        if not encendido:
            print(f"{OK} Apagado por {var}=false: no se verifica.")
            resultados.append((titulo, "apagado"))
            continue
        resultados.append((titulo, chequeo()))

    print("\n-- Resultado --------------------------------------")
    for titulo, estado in resultados:
        # 15 y no 9: el ancho viejo entraba para `X:`, `LinkedIn:` y `FRED:`,
        # pero `Alpha Vantage:` mide 14 y `Anthropic:` 10, asi que la columna
        # salia dentada. Este es el titulo mas largo de los seis mas un espacio.
        print(f"{titulo + ':':<15} {estado}")

    if all(estado == "apagado" for _, estado in resultados):
        print("\nTodo apagado: no hay ninguna credencial que verificar.")
        return 0
    if any(estado == NO_LISTO for _, estado in resultados):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
