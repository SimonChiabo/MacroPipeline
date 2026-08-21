"""
Redacción de secretos en los logs.

Varias APIs del pipeline exigen la credencial como query param (FRED con
`api_key`, FMP con `apikey`), así que la URL completa lleva el secreto dentro.
Cuando urllib3 reintenta una petición imprime esa URL en un warning, y ese
warning viaja por el logging estándar hasta cualquier handler configurado,
incluido el exportador hacia Grafana. Sin este filtro, las keys terminan en la
plataforma de observabilidad.

El filtro actúa a nivel de handler, que es el punto de paso obligado: cubre
tanto los logs propios (structlog) como los de librerías de terceros.
"""

import logging
import os
import re
from collections.abc import Iterable

REDACTED = "***REDACTED***"

# Valores más cortos que esto producirían falsos positivos por todo el log.
_MIN_SECRET_LENGTH = 8

# Nombres de variables de entorno que denotan un secreto.
_SECRET_NAME_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE
)

# Credencial pasada como query param. Las alternativas van de más larga a más
# corta para que `access_token` no sea capturado por `token`.
_PARAM_PATTERN = re.compile(
    r"(?i)\b(access_?token|api_?key|apikey|token|secret|password)=([^&\s\"'<>]+)"
)

# Cabecera Authorization, tanto en formato HTTP como en el par clave=valor que
# usa OTEL_EXPORTER_OTLP_HEADERS.
_AUTH_PATTERN = re.compile(r"(?i)(authorization[=:]\s*)(basic|bearer)(\s+)(\S+)")


def collect_secret_values(env: dict[str, str] | None = None) -> list[str]:
    """
    Extrae del entorno los valores que deben redactarse.

    Solo considera variables cuyo nombre denota un secreto: redactar el endpoint
    de OTLP o la ruta de la base de datos arruinaría los logs de diagnóstico sin
    proteger nada.
    """
    env = os.environ if env is None else env

    return [
        value
        for name, value in env.items()
        if value
        and len(value) >= _MIN_SECRET_LENGTH
        and _SECRET_NAME_PATTERN.search(name)
    ]


def redact(text: str, secrets: Iterable[str] | None = None) -> str:
    """
    Devuelve el texto con las credenciales sustituidas por REDACTED.

    Combina dos estrategias porque ninguna basta sola:
    - Por patrón: cubre credenciales que no están en el entorno (una key pasada
      como argumento, la de otro tenant, la de un test).
    - Por valor conocido: cubre los secretos donde el patrón no llega, por
      ejemplo dentro del texto de una excepción.
    """
    values = collect_secret_values() if secrets is None else list(secrets)

    result = _PARAM_PATTERN.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    result = _AUTH_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", result
    )

    for value in values:
        if value and len(value) >= _MIN_SECRET_LENGTH:
            result = result.replace(value, REDACTED)

    return result


class RedactionFilter(logging.Filter):
    """
    Filtro de logging que elimina credenciales de cada registro.

    Trabaja sobre el mensaje ya formateado: urllib3 pasa la URL como argumento
    (`log.warning("... %s", url)`), de modo que inspeccionar solo `record.msg`
    dejaría la key intacta.
    """

    def __init__(self, secrets: Iterable[str] | None = None):
        super().__init__()
        self.secrets = collect_secret_values() if secrets is None else list(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - un log roto no debe tumbar el pipeline
            return True

        redacted = redact(message, secrets=self.secrets)
        if redacted != message:
            record.msg = redacted
            record.args = ()

        return True


def install_redaction_filter(secrets: Iterable[str] | None = None) -> None:
    """
    Instala el filtro en todos los handlers del logger raíz.

    Idempotente: llamarlo dos veces no duplica el filtro. Debe invocarse después
    de configurar el logging (basicConfig o equivalente), para que los handlers
    ya existan.
    """
    root = logging.getLogger()
    handlers = root.handlers or []

    if not handlers:
        logging.getLogger(__name__).warning(
            "redaction_filter_not_installed_no_handlers"
        )
        return

    for handler in handlers:
        if any(isinstance(f, RedactionFilter) for f in handler.filters):
            continue
        handler.addFilter(RedactionFilter(secrets=secrets))
