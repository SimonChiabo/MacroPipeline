import os
import boto3
import structlog
from botocore.exceptions import ClientError
from typing import Optional

logger = structlog.get_logger(__name__)

class R2ClientError(Exception):
    """Excepciones específicas para la capa de almacenamiento R2."""
    pass

class R2Client:
    """Cliente para subir objetos a Cloudflare R2 usando la API de S3 (boto3)."""
    
    def __init__(self, account_id: Optional[str] = None, access_key: Optional[str] = None, secret_key: Optional[str] = None, bucket: Optional[str] = None):
        self.account_id = account_id or os.environ.get("R2_ACCOUNT_ID")
        self.access_key = access_key or os.environ.get("R2_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("R2_SECRET_ACCESS_KEY")
        self.bucket = bucket or os.environ.get("R2_BUCKET_NAME", "macropipeline-snapshots")
        
        if not all([self.account_id, self.access_key, self.secret_key]):
            raise ValueError("Faltan credenciales de Cloudflare R2 (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY).")
            
        endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
        
        # R2 utiliza la interfaz estándar de S3
        self.s3 = boto3.client(
            service_name="s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="auto" # Cloudflare R2 requiere 'auto' o 'us-east-1'
        )

    def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """
        Sube los bytes de una imagen al bucket de R2.
        Retorna la pseudo-URL (URI S3) del objeto.
        """
        logger.info("uploading_image_to_r2", filename=filename, size_bytes=len(image_bytes))
        
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=filename,
                Body=image_bytes,
                ContentType="image/png"
            )
            logger.info("image_uploaded_successfully", bucket=self.bucket, key=filename)
            return f"r2://{self.bucket}/{filename}"
        except ClientError as e:
            logger.error("r2_upload_failed", error=str(e))
            raise R2ClientError(f"Error subiendo a R2: {e}") from e
