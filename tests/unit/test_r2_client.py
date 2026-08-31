"""Tests del cliente de R2.

Hasta ahora `r2_client.py` no tenia ningun test unitario: lo unico que lo
tocaba era `tests/integration/test_orchestrator_persistence.py`, de refilon y
siempre con el cliente entero mockeado. El fichero nace acá porque el estado
del pipeline pasa a viajar por R2, o sea que este cliente deja de sostener un
snapshot prescindible y pasa a sostener la deduplicación.

Lo que más importa acá es **la distinción entre una key ausente y un fallo de
transporte**. Es la que gobierna la tabla de decisión entera del sincronizado:
una key ausente es una primera corrida (se sigue), un fallo de transporte deja
al pipeline sin saber si ya publicó (se aborta). Confundirlas publicaría dos
veces.
"""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from macro_pipeline.storage.r2_client import R2Client, R2ClientError

_CREDS = {
    "account_id": "cuenta",
    "access_key": "key",
    "secret_key": "secreto",
    "bucket": "bucket-de-prueba",
}


def _client(monkeypatch, fake_s3):
    """Un R2Client con credenciales explícitas y el `boto3.client` mockeado."""
    monkeypatch.setattr(
        "macro_pipeline.storage.r2_client.boto3.client", lambda **kw: fake_s3
    )
    return R2Client(**_CREDS)


class _FakeS3:
    """Doble de `boto3.client('s3')` con solo lo que usa R2Client."""

    def __init__(self, get_result=None, get_error=None, put_error=None):
        self._get_result = get_result
        self._get_error = get_error
        self._put_error = put_error
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        if self._put_error is not None:
            raise self._put_error

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        if self._get_error is not None:
            raise self._get_error
        return {"Body": _FakeBody(self._get_result)}


class _FakeBody:
    def __init__(self, payload: bytes | None):
        self._payload = payload or b""

    def read(self) -> bytes:
        return self._payload


def _client_error(code: str, operation: str = "GetObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": f"simulado: {code}"}}, operation
    )


# ── upload_object ────────────────────────────────────────────────────────────


def test_upload_object_manda_bytes_y_content_type(monkeypatch):
    s3 = _FakeS3()
    cliente = _client(monkeypatch, s3)

    cliente.upload_object("state/state.db", b"contenido", "application/octet-stream")

    assert s3.put_calls == [
        {
            "Bucket": "bucket-de-prueba",
            "Key": "state/state.db",
            "Body": b"contenido",
            "ContentType": "application/octet-stream",
        }
    ]


def test_upload_object_traduce_un_clienterror(monkeypatch):
    s3 = _FakeS3(put_error=_client_error("AccessDenied", "PutObject"))
    cliente = _client(monkeypatch, s3)

    with pytest.raises(R2ClientError):
        cliente.upload_object("state/state.db", b"x", "application/octet-stream")


def test_upload_object_traduce_un_fallo_de_transporte(monkeypatch):
    """Un corte de red no es `ClientError` y tambien tiene que salir traducido.

    `EndpointConnectionError` hereda de `BotoCoreError`, no de `ClientError`:
    no hay herencia entre las dos ramas. Atrapar solo `ClientError` dejaria
    escapar el fallo mas probable, que es la divergencia (b) de ADR-009 otra
    vez.
    """
    s3 = _FakeS3(put_error=EndpointConnectionError(endpoint_url="https://r2.test"))
    cliente = _client(monkeypatch, s3)

    with pytest.raises(R2ClientError):
        cliente.upload_object("state/state.db", b"x", "application/octet-stream")


# ── download_object ──────────────────────────────────────────────────────────


def test_download_object_devuelve_los_bytes(monkeypatch):
    s3 = _FakeS3(get_result=b"sqlite-bytes")
    cliente = _client(monkeypatch, s3)

    assert cliente.download_object("state/state.db") == b"sqlite-bytes"
    assert s3.get_calls == [{"Bucket": "bucket-de-prueba", "Key": "state/state.db"}]


@pytest.mark.parametrize("codigo", ["NoSuchKey", "404", "NoSuchBucket"])
def test_download_object_devuelve_none_si_no_existe(monkeypatch, codigo):
    """Ausencia se reporta como None, no como excepcion.

    `NoSuchKey` es lo que devuelve S3S en `get_object`; el `404` esta porque
    algunos servicios compatibles lo usan en su lugar y R2 no promete cual.
    `NoSuchBucket` entra por la misma puerta: el bucket todavia sin crear es
    indistinguible, para el pipeline, de un estado remoto que aun no existe.
    """
    s3 = _FakeS3(get_error=_client_error(codigo))
    cliente = _client(monkeypatch, s3)

    assert cliente.download_object("state/state.db") is None


def test_download_object_levanta_ante_un_clienterror_que_no_es_ausencia(monkeypatch):
    """Un permiso denegado NO es una key ausente.

    Es la confusion que publicaria dos veces: si `AccessDenied` se leyera como
    "todavia no hay estado", el pipeline arrancaria con la base vacia y
    republicaria el cierre de la semana.
    """
    s3 = _FakeS3(get_error=_client_error("AccessDenied"))
    cliente = _client(monkeypatch, s3)

    with pytest.raises(R2ClientError):
        cliente.download_object("state/state.db")


def test_download_object_levanta_ante_un_fallo_de_transporte(monkeypatch):
    """Mismo razonamiento que el permiso denegado, por la rama de BotoCoreError."""
    s3 = _FakeS3(get_error=EndpointConnectionError(endpoint_url="https://r2.test"))
    cliente = _client(monkeypatch, s3)

    with pytest.raises(R2ClientError):
        cliente.download_object("state/state.db")


# ── upload_image sigue igual ─────────────────────────────────────────────────


def test_upload_image_conserva_su_firma_y_su_retorno(monkeypatch):
    """`upload_image` pasa a delegar, pero nada de lo que ve el orquestador cambia."""
    s3 = _FakeS3()
    cliente = _client(monkeypatch, s3)

    url = cliente.upload_image(b"png-bytes", "cierre.png")

    assert url == "r2://bucket-de-prueba/cierre.png"
    assert s3.put_calls == [
        {
            "Bucket": "bucket-de-prueba",
            "Key": "cierre.png",
            "Body": b"png-bytes",
            "ContentType": "image/png",
        }
    ]


def test_upload_image_sigue_traduciendo_el_error(monkeypatch):
    s3 = _FakeS3(put_error=_client_error("AccessDenied", "PutObject"))
    cliente = _client(monkeypatch, s3)

    with pytest.raises(R2ClientError):
        cliente.upload_image(b"png-bytes", "cierre.png")
