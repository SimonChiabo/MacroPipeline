import os

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError

logger = structlog.get_logger(__name__)

# Códigos con los que R2 responde a un objeto que no está.
#
# **Verificado en vivo el 2026-08-31** contra el bucket real
# (`macropipeline-snapshots`), no deducido de la API de S3: R2 devuelve
# `NoSuchKey` con `HTTPStatusCode` 404. El `404` a secas se queda igual porque
# algunos servicios compatibles lo ponen en `Code`, y no cuesta nada.
# `NoSuchBucket` entra por la misma puerta: un bucket todavía sin crear es
# indistinguible, para quien llama, de un objeto que aún no existe — y la
# corrida muere igual en el primer push, antes de publicar nada.
#
# Todo lo demás —`AccessDenied` el primero— es un error de verdad. Leer un
# permiso denegado como "todavía no hay nada" es lo que haría que el pipeline
# arrancara con el estado vacío y republicara el cierre de la semana.
_CODIGOS_DE_AUSENCIA = frozenset({"NoSuchKey", "NoSuchBucket", "404"})


class R2ClientError(Exception):
    """Excepciones específicas para la capa de almacenamiento R2."""

    pass


class R2Client:
    """Cliente para subir objetos a Cloudflare R2 usando la API de S3 (boto3)."""

    def __init__(
        self,
        account_id: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
    ):
        self.account_id = account_id or os.environ.get("R2_ACCOUNT_ID")
        self.access_key = access_key or os.environ.get("R2_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("R2_SECRET_ACCESS_KEY")
        self.bucket = bucket or os.environ.get(
            "R2_BUCKET_NAME", "macropipeline-snapshots"
        )

        if not all([self.account_id, self.access_key, self.secret_key]):
            raise ValueError(
                "Faltan credenciales de Cloudflare R2 (R2_ACCOUNT_ID, "
                "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)."
            )

        endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"

        # R2 utiliza la interfaz estándar de S3
        self.s3 = boto3.client(
            service_name="s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="auto",  # Cloudflare R2 requiere 'auto' o 'us-east-1'
        )

    # ── Objetos genéricos ─────────────────────────────────────────────────────

    def upload_object(self, key: str, body: bytes, content_type: str) -> None:
        """Sube bytes arbitrarios a `key`.

        Atrapa las **dos** ramas de botocore. `ClientError` y `BotoCoreError`
        son hermanas: no hay herencia entre ellas, así que un `except
        ClientError` a secas deja escapar `EndpointConnectionError` —el corte
        de red, que es el fallo más probable—. Es la divergencia (b) de
        ADR-009 aplicada acá desde el principio.
        """
        try:
            self.s3.put_object(
                Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as e:
            logger.error("r2_upload_failed", key=key, error=str(e))
            raise R2ClientError(f"Error subiendo a R2: {e}") from e

    def download_object(self, key: str) -> bytes | None:
        """Baja `key`. Devuelve None si el objeto no existe.

        La distinción entre ausencia y fallo es la que gobierna la tabla de
        decisión del sincronizado de estado: una key ausente es una primera
        corrida y se sigue, un fallo deja al pipeline sin saber si ya publicó
        y hay que abortar. Por eso la ausencia sale como None y todo lo demás
        levanta.
        """
        try:
            respuesta = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            codigo = str(e.response.get("Error", {}).get("Code", ""))
            if codigo in _CODIGOS_DE_AUSENCIA:
                logger.info("r2_object_absent", key=key, code=codigo)
                return None
            logger.error("r2_download_failed", key=key, error=str(e))
            raise R2ClientError(f"Error bajando de R2: {e}") from e
        except BotoCoreError as e:
            # Rama separada a propósito: un fallo de transporte no trae
            # `response`, así que no se le puede preguntar el código.
            logger.error("r2_download_failed", key=key, error=str(e))
            raise R2ClientError(f"Error bajando de R2: {e}") from e

        cuerpo: bytes = respuesta["Body"].read()
        return cuerpo

    # ── Imágenes ──────────────────────────────────────────────────────────────

    def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """
        Sube los bytes de una imagen al bucket de R2.
        Retorna la pseudo-URL (URI S3) del objeto.
        """
        logger.info(
            "uploading_image_to_r2", filename=filename, size_bytes=len(image_bytes)
        )

        self.upload_object(filename, image_bytes, "image/png")

        logger.info("image_uploaded_successfully", bucket=self.bucket, key=filename)
        return f"r2://{self.bucket}/{filename}"
