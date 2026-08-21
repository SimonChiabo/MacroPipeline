from __future__ import annotations

import os
import sqlite3
import structlog
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - solo para tipado
    from macro_pipeline.validators.schemas import MacroSnapshot

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = str(Path.home() / ".macropipeline" / "state.db")


class StateDB:
    """
    Gestor de estado local basado en SQLite.
    - Ruta configurable via STATE_DB_PATH (no depende del CWD).
    - Schema extendido: data_source, post_ids, prompt_version, headline, status.
    - Migración automática para bases de datos existentes.
    - Permite reconciliación en caso de fallo parcial de publicación.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("STATE_DB_PATH", _DEFAULT_DB_PATH)
        # Garantizar que el directorio padre existe
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_db()
        logger.info("state_db_initialized", path=self.db_path)

    def _init_db(self) -> None:
        """Crea la tabla con el schema completo si no existe."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published_events (
                    event_id            TEXT PRIMARY KEY,
                    status              TEXT NOT NULL DEFAULT 'in_progress',
                    created_at          TIMESTAMP NOT NULL,
                    published_at        TIMESTAMP,
                    data_source         TEXT,
                    sp500_close         REAL,
                    nasdaq_close        REAL,
                    sp500_return        REAL,
                    nasdaq_return       REAL,
                    prompt_version      TEXT,
                    headline            TEXT,
                    validator_approved  INTEGER,
                    cpi_yoy             REAL,
                    cpi_as_of           TEXT,
                    unemployment_rate   REAL,
                    unrate_as_of        TEXT,
                    treasury_10y        REAL,
                    dgs10_as_of         TEXT,
                    image_url           TEXT,
                    x_post_id           TEXT,
                    linkedin_post_id    TEXT
                )
            """)

    def _migrate_db(self) -> None:
        """Añade columnas nuevas a una base de datos existente (idempotente)."""
        new_cols = [
            ("status",              "TEXT NOT NULL DEFAULT 'published'"),
            ("data_source",         "TEXT"),
            ("sp500_close",         "REAL"),
            ("nasdaq_close",        "REAL"),
            ("sp500_return",        "REAL"),
            ("nasdaq_return",       "REAL"),
            ("prompt_version",      "TEXT"),
            ("headline",            "TEXT"),
            ("validator_approved",  "INTEGER"),
            ("cpi_yoy",             "REAL"),
            ("cpi_as_of",           "TEXT"),
            ("unemployment_rate",   "REAL"),
            ("unrate_as_of",        "TEXT"),
            ("treasury_10y",        "REAL"),
            ("dgs10_as_of",         "TEXT"),
            ("x_post_id",           "TEXT"),
            ("linkedin_post_id",    "TEXT"),
        ]
        with sqlite3.connect(self.db_path) as conn:
            for col, col_type in new_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE published_events ADD COLUMN {col} {col_type}"
                    )
                except sqlite3.OperationalError:
                    pass  # columna ya existe

    # ── In-progress (locking ligero) ─────────────────────────────────────────

    def is_in_progress(self, event_id: str) -> bool:
        """Devuelve True si hay una run activa para este event_id."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM published_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None and row[0] == "in_progress"

    def mark_in_progress(self, event_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO published_events "
                "(event_id, status, created_at) VALUES (?, 'in_progress', ?)",
                (event_id, datetime.utcnow()),
            )
        logger.info("event_marked_in_progress", event_id=event_id)

    # ── Publicado ─────────────────────────────────────────────────────────────

    def is_published(self, event_id: str) -> bool:
        """Devuelve True solo si el evento está completamente publicado."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM published_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        is_pub = row is not None and row[0] == "published"
        logger.debug("checking_event_status", event_id=event_id, is_published=is_pub)
        return is_pub

    def get_publication_state(self, event_id: str) -> Dict[str, Any]:
        """Devuelve el estado completo para reconciliación tras fallo parcial."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM published_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row else {}

    def mark_x_published(self, event_id: str, x_post_id: str) -> None:
        """Persiste el post_id de X inmediatamente tras la publicación."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE published_events SET x_post_id = ? WHERE event_id = ?",
                (x_post_id, event_id),
            )
        logger.info("x_post_id_persisted", event_id=event_id, x_post_id=x_post_id)

    def mark_linkedin_published(self, event_id: str, linkedin_post_id: str) -> None:
        """Persiste el post_id de LinkedIn inmediatamente tras la publicación."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE published_events SET linkedin_post_id = ? WHERE event_id = ?",
                (linkedin_post_id, event_id),
            )
        logger.info(
            "linkedin_post_id_persisted",
            event_id=event_id,
            linkedin_post_id=linkedin_post_id,
        )

    def mark_as_published(
        self,
        event_id: str,
        image_url: Optional[str] = None,
        data_source: Optional[str] = None,
        sp500_close: Optional[float] = None,
        nasdaq_close: Optional[float] = None,
        sp500_return: Optional[float] = None,
        nasdaq_return: Optional[float] = None,
        prompt_version: Optional[str] = None,
        headline: Optional[str] = None,
        validator_approved: Optional[bool] = None,
        macro: Optional[MacroSnapshot] = None,
    ) -> None:
        """Cierra la run y persiste sus metadatos.

        `macro` es opcional a proposito: `safe_build_macro_snapshot`
        devuelve None cuando FRED falla, y el cierre semanal se publica
        igual. En ese caso las seis columnas macro quedan en NULL.

        Gana la ultima escritura: una segunda llamada con `macro=None`
        sobrescribe con NULL los valores que hubiera. Es la misma semantica
        que ya tenian `headline` e `image_url`, y no es alcanzable desde el
        orquestador porque `is_published` corta la re-ejecucion de un evento
        ya publicado.
        """
        # Las fechas van como TEXT ISO explicito: los adaptadores
        # implicitos de date/datetime estan deprecados desde Python 3.12.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE published_events SET
                    status = 'published', published_at = ?,
                    image_url = ?, data_source = ?,
                    sp500_close = ?, nasdaq_close = ?,
                    sp500_return = ?, nasdaq_return = ?,
                    prompt_version = ?, headline = ?,
                    validator_approved = ?,
                    cpi_yoy = ?, cpi_as_of = ?,
                    unemployment_rate = ?, unrate_as_of = ?,
                    treasury_10y = ?, dgs10_as_of = ?
                WHERE event_id = ?""",
                (
                    datetime.utcnow(),
                    image_url,
                    data_source,
                    sp500_close,
                    nasdaq_close,
                    sp500_return,
                    nasdaq_return,
                    prompt_version,
                    headline,
                    int(validator_approved) if validator_approved is not None else None,
                    macro.cpi_yoy if macro else None,
                    macro.cpi_as_of.isoformat() if macro else None,
                    macro.unemployment_rate if macro else None,
                    macro.unrate_as_of.isoformat() if macro else None,
                    macro.treasury_10y if macro else None,
                    macro.dgs10_as_of.isoformat() if macro else None,
                    event_id,
                ),
            )
        logger.info(
            "event_marked_as_published",
            event_id=event_id,
            data_source=data_source,
            macro_persisted=macro is not None,
        )

    # ── Fallos y expiración ───────────────────────────────────────────────────

    def mark_failed(self, event_id: str, reason: str = "") -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE published_events SET status = 'failed' WHERE event_id = ?",
                (event_id,),
            )
        logger.warning("event_marked_failed", event_id=event_id, reason=reason)

    def mark_expired(self, event_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE published_events SET status = 'expired' WHERE event_id = ?",
                (event_id,),
            )
        logger.warning("event_marked_expired", event_id=event_id)
