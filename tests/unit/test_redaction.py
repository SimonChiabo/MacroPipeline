import io
import logging

import pytest

from macro_pipeline.observability.redaction import (
    REDACTED,
    RedactionFilter,
    collect_secret_values,
    install_redaction_filter,
    redact,
)

# URL real que urllib3 imprimió al reintentar contra FRED, con la key expuesta.
FRED_RETRY_URL = (
    "/fred/series/observations?series_id=UNRATE"
    "&api_key=1c60c60fafdb773bd33904fbe978b77b&file_type=json"
)


def test_redacts_api_key_query_param():
    out = redact(FRED_RETRY_URL, secrets=[])

    assert "1c60c60fafdb773bd33904fbe978b77b" not in out
    assert REDACTED in out
    # El resto de la URL debe sobrevivir: sirve para diagnosticar
    assert "series_id=UNRATE" in out
    assert "file_type=json" in out


def test_redacts_apikey_param_without_underscore():
    """FMP usa `apikey`, FRED usa `api_key`: ambos deben caer."""
    out = redact(
        "https://financialmodelingprep.com/stable/quote?apikey=abc123XYZ789", secrets=[]
    )

    assert "abc123XYZ789" not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "header",
    [
        "Authorization: Bearer sk-ant-api03-verysecretvalue",
        "Authorization=Basic OTk5OTk5OmdsY19zZWNyZXQ=",
    ],
)
def test_redacts_authorization_headers(header):
    out = redact(header, secrets=[])

    assert "secret" not in out.lower().replace(REDACTED.lower(), "")
    assert REDACTED in out


def test_redacts_known_secret_value_anywhere():
    """Un secreto puede aparecer fuera de una query string, ej. en un traceback."""
    secret = "1c60c60fafdb773bd33904fbe978b77b"
    out = redact(f"ValueError: la key {secret} fue rechazada", secrets=[secret])

    assert secret not in out
    assert REDACTED in out


def test_leaves_ordinary_text_untouched():
    text = "fred_series_success series_id=CPIAUCSL records=955"

    assert redact(text, secrets=[]) == text


def test_collect_secret_values_picks_secret_named_vars_only():
    env = {
        "FRED_API_KEY": "1c60c60fafdb773bd33904fbe978b77b",
        "TELEGRAM_BOT_TOKEN": "123456:AAbbccddeeffgghh",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.grafana.net/otlp",
        "STATE_DB_PATH": "/tmp/state.db",
        "ALLOW_MOCK_DATA": "false",
    }

    values = collect_secret_values(env)

    assert "1c60c60fafdb773bd33904fbe978b77b" in values
    assert "123456:AAbbccddeeffgghh" in values
    # No son secretos: redactarlos arruinaría los logs de diagnóstico
    assert "https://otlp.grafana.net/otlp" not in values
    assert "/tmp/state.db" not in values
    assert "false" not in values


def test_collect_secret_values_ignores_short_values():
    """Valores cortos generarían falsos positivos por todo el log."""
    assert collect_secret_values({"API_KEY": "abc"}) == []


def _capture(logger_name, secrets, emit):
    """Emite un log con el filtro instalado y devuelve la salida del handler."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter(secrets=secrets))

    logger = logging.getLogger(logger_name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)

    emit(logger)
    handler.flush()
    return stream.getvalue()


def test_filter_redacts_url_passed_as_log_argument():
    """
    urllib3 loguea la URL como argumento (%s), no dentro del mensaje.
    Redactar solo record.msg dejaría la key intacta: este es el caso real.
    """
    out = _capture(
        "test.urllib3",
        secrets=[],
        emit=lambda log: log.warning(
            "Retrying after connection broken by %r: %s",
            "ReadTimeoutError",
            FRED_RETRY_URL,
        ),
    )

    assert "1c60c60fafdb773bd33904fbe978b77b" not in out
    assert REDACTED in out
    assert "Retrying" in out


def test_filter_redacts_secret_in_message_body():
    secret = "1c60c60fafdb773bd33904fbe978b77b"
    out = _capture(
        "test.body",
        secrets=[secret],
        emit=lambda log: log.warning("fallo con la key %s", secret),
    )

    assert secret not in out
    assert REDACTED in out


def test_filter_leaves_clean_records_untouched():
    out = _capture(
        "test.clean",
        secrets=[],
        emit=lambda log: log.warning("macro_snapshot_built cpi_yoy=%s", 0.034),
    )

    assert "macro_snapshot_built cpi_yoy=0.034" in out


def test_install_redaction_filter_is_idempotent():
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    try:
        install_redaction_filter()
        install_redaction_filter()

        installed = [f for f in handler.filters if isinstance(f, RedactionFilter)]
        assert len(installed) == 1
    finally:
        root.removeHandler(handler)
