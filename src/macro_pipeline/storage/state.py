import sqlite3
import structlog
from datetime import datetime
from typing import Optional

logger = structlog.get_logger(__name__)

class StateDB:
    """Gestor de estado local basado en SQLite para evitar publicar duplicados."""
    
    def __init__(self, db_path: str = "macropipeline.db"):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        """Inicializa la tabla si no existe."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published_events (
                    event_id TEXT PRIMARY KEY,
                    published_at TIMESTAMP NOT NULL,
                    image_url TEXT
                )
            """)
            
    def is_published(self, event_id: str) -> bool:
        """Verifica si un evento ya fue publicado (ej. 'weekly_close_2026-05-14')."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM published_events WHERE event_id = ?", (event_id,))
            result = cursor.fetchone()
            
        is_pub = result is not None
        logger.debug("checking_event_status", event_id=event_id, is_published=is_pub)
        return is_pub
        
    def mark_as_published(self, event_id: str, image_url: Optional[str] = None):
        """Marca un evento como publicado exitosamente para bloquear futuras ejecuciones idénticas."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO published_events (event_id, published_at, image_url) VALUES (?, ?, ?)",
                (event_id, datetime.utcnow(), image_url)
            )
        logger.info("event_marked_as_published", event_id=event_id)
