"""Hace que el fichero de estado sobreviva a un entorno efímero.

ADR-002 promete idempotencia apoyada en que el `event_id` ya publicado siga en
SQLite, pero decide que Routines clona el repo y ejecuta en un entorno
gestionado por Anthropic. Si ese entorno es efímero, el fichero no sobrevive
entre corridas con **ninguna** ruta —`STATE_DB_PATH` apunta al mismo
filesystem— y `is_published()` devuelve False siempre, sin error y sin log que
lo distinga de una primera ejecución legítima.

Esto sube y baja el fichero entero por R2. El fichero entero y no un objeto por
evento a propósito: `state.py` codifica invariantes que costaron caro (el
re-arme del lock sobre `failed`/`expired`, la guarda de `published` en
`mark_failed`, la reconciliación por red) y reimplementar esa máquina en otro
formato reexpone cada bug ya arreglado. De regalo viajan todas las columnas de
metadatos, que es lo que sostiene la reproducibilidad de ADR-007.

**El reparto de responsabilidades es el punto de este módulo:**

- `pull()` **nunca levanta**. Corre al principio de la corrida, antes de que
  exista el canal de alerta, así que anota el motivo y devuelve; quien decide y
  avisa es el punto de decisión del orquestador. Es el patrón que dejó el punto
  13, donde el orden de construcción haciendo de lógica fue la causa raíz.
- `push()` **sí levanta**. Corre con la corrida en marcha, donde el manejador
  general la atrapa y cierra la fila. Un push que falla en silencio haría que
  la corrida siguiente leyera un estado viejo y republicara lo que ya salió.

Quien llama es responsable de volver a asegurar el esquema después de un pull:
`_init_db` y `_migrate_db` corrieron sobre el fichero que había antes, no sobre
el que acaba de bajar.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import structlog

from macro_pipeline.storage.r2_client import R2Client

logger = structlog.get_logger(__name__)

_DEFAULT_KEY = "state/state.db"


class StateSyncError(Exception):
    """Falló la subida del estado a R2."""

    pass


class StateSync:
    """Sube y baja el fichero de `StateDB` contra R2."""

    def __init__(
        self, r2_client: R2Client, db_path: str, key: str = _DEFAULT_KEY
    ) -> None:
        self.r2 = r2_client
        self.db_path = Path(db_path)
        self.key = key
        self.remote_absent = False

    # ── Bajada ────────────────────────────────────────────────────────────────

    def pull(self) -> str | None:
        """Trae el estado remoto. Devuelve el motivo del fallo, o None.

        None significa las dos cosas buenas: bajó bien, o no había nada que
        bajar. Las distingue `remote_absent`, porque no son lo mismo para quien
        avisa —un remoto ausente es "primera corrida o pérdida"— pero sí lo son
        para seguir adelante.
        """
        try:
            contenido = self.r2.download_object(self.key)
        except Exception as e:
            # Ancho a propósito: el motivo viaja como texto y lo reporta otro.
            # Levantar acá mataría la corrida antes de que exista el canal.
            logger.error("state_pull_failed", key=self.key, error=str(e))
            return f"No se pudo bajar el estado de R2: {e}"

        if contenido is None:
            # El fichero local se queda como está, a propósito: en un runner
            # está vacío igual, y en una máquina con historial ese estado
            # siembra el remoto en el primer push. Borrarlo no compraría nada.
            self.remote_absent = True
            logger.warning("state_remote_absent", key=self.key)
            return None

        return self._escribir_verificando(contenido)

    def _escribir_verificando(self, contenido: bytes) -> str | None:
        """Escribe en un temporal, verifica que sea SQLite y recién reemplaza.

        Escribir directo sobre `self.db_path` dejaría la base ilegible si la
        bajada viniera cortada. El temporal va **en el mismo directorio**
        porque `os.replace` solo es atómico dentro del mismo filesystem, y
        `os.replace` y no `rename` porque en Windows renombrar sobre un fichero
        que existe levanta `FileExistsError`.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temporal = self.db_path.with_suffix(self.db_path.suffix + ".descarga")

        try:
            temporal.write_bytes(contenido)
            self._verificar_sqlite(temporal)
        except Exception as e:
            logger.error("state_pull_corrupt", key=self.key, error=str(e))
            temporal.unlink(missing_ok=True)
            return f"El estado bajado de R2 no es una base valida: {e}"

        os.replace(temporal, self.db_path)
        logger.info(
            "state_pulled",
            key=self.key,
            size_bytes=len(contenido),
            path=str(self.db_path),
        )
        return None

    @staticmethod
    def _verificar_sqlite(ruta: Path) -> None:
        """Levanta si el fichero no es una base SQLite legible.

        La conexión se cierra sí o sí: en Windows un handle abierto impide el
        `os.replace` de más arriba.
        """
        conn = sqlite3.connect(ruta)
        try:
            conn.execute("PRAGMA schema_version").fetchone()
        finally:
            conn.close()

    # ── Subida ────────────────────────────────────────────────────────────────

    def push(self) -> None:
        """Sube el fichero local. Levanta `StateSyncError` si falla."""
        if not self.db_path.exists():
            # Sin base local no hay nada que sincronizar. Subir un fichero
            # vacío borraría el estado remoto, que es lo contrario de lo que
            # este módulo existe para hacer.
            logger.warning("state_push_skipped_no_local", path=str(self.db_path))
            return

        contenido = self.db_path.read_bytes()
        try:
            self.r2.upload_object(self.key, contenido, "application/octet-stream")
        except Exception as e:
            logger.error("state_push_failed", key=self.key, error=str(e))
            raise StateSyncError(f"No se pudo subir el estado a R2: {e}") from e

        logger.info("state_pushed", key=self.key, size_bytes=len(contenido))
