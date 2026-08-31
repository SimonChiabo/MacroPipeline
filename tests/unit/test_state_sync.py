"""El sincronizado del fichero de estado contra R2.

`StateDB` vive en un fichero local. ADR-002 decide que Routines clona el repo
y ejecuta en un entorno gestionado: si ese entorno es efimero, el fichero no
sobrevive entre corridas e `is_published()` devuelve False siempre. Esto es lo
que lo hace sobrevivir.

La reparticion de responsabilidades es deliberada y es lo que mas se testea
aca: **`pull()` nunca levanta, `push()` si**. El pull corre antes de que exista
el canal de alerta, asi que anota el motivo y deja decidir al punto de decision
del orquestador — el mismo patron que dejo el punto 13, donde el orden de
construccion haciendo de logica fue la causa raiz. El push corre con la corrida
ya en marcha, donde una excepcion tiene quien la atrape.
"""

import sqlite3
from pathlib import Path

import pytest

from macro_pipeline.storage.r2_client import R2ClientError
from macro_pipeline.storage.state_sync import StateSync, StateSyncError


class _FakeR2:
    """Doble de R2Client con solo lo que usa StateSync."""

    def __init__(self, contenido=None, download_error=None, upload_error=None):
        self._contenido = contenido
        self._download_error = download_error
        self._upload_error = upload_error
        self.subidas: list[tuple[str, bytes]] = []
        self.bajadas: list[str] = []

    def download_object(self, key: str) -> bytes | None:
        self.bajadas.append(key)
        if self._download_error is not None:
            raise self._download_error
        return self._contenido

    def upload_object(self, key: str, body: bytes, content_type: str) -> None:
        self.subidas.append((key, body))
        if self._upload_error is not None:
            raise self._upload_error


def _base_valida(ruta: Path, event_id: str = "weekly_close_2026-08-28") -> bytes:
    """Devuelve los bytes de un SQLite con una fila publicada."""
    conn = sqlite3.connect(ruta)
    conn.execute(
        "CREATE TABLE published_events (event_id TEXT PRIMARY KEY, status TEXT)"
    )
    conn.execute("INSERT INTO published_events VALUES (?, 'published')", (event_id,))
    conn.commit()
    conn.close()
    return ruta.read_bytes()


# ── pull: nunca levanta ──────────────────────────────────────────────────────


def test_pull_trae_el_estado_remoto(tmp_path):
    remoto = _base_valida(tmp_path / "remoto.db")
    local = tmp_path / "state.db"
    sync = StateSync(_FakeR2(contenido=remoto), str(local))

    assert sync.pull() is None
    assert local.exists()

    filas = (
        sqlite3.connect(local)
        .execute("SELECT event_id FROM published_events")
        .fetchall()
    )
    assert filas == [("weekly_close_2026-08-28",)]


def test_pull_devuelve_el_motivo_ante_un_fallo_de_transporte(tmp_path):
    """No levanta: anota. Quien decide es el punto de decision del orquestador.

    Levantar aca mataria la corrida antes de que exista el canal de alerta,
    que es exactamente la forma del punto 13.
    """
    sync = StateSync(
        _FakeR2(download_error=R2ClientError("Error bajando de R2: timeout")),
        str(tmp_path / "state.db"),
    )

    motivo = sync.pull()

    assert motivo is not None
    assert "timeout" in motivo


def test_pull_con_el_remoto_ausente_no_es_un_fallo(tmp_path):
    sync = StateSync(_FakeR2(contenido=None), str(tmp_path / "state.db"))

    assert sync.pull() is None
    assert sync.remote_absent is True


def test_pull_con_el_remoto_ausente_no_toca_el_fichero_local(tmp_path):
    """El local sobrevive intacto, y es el camino de migracion.

    En un runner el local esta vacio igual. En la maquina de Simon, el estado
    que ya existe siembra el remoto en el primer push. Borrarlo no compraria
    nada y perderia el historial que hay.
    """
    local = tmp_path / "state.db"
    _base_valida(local, event_id="weekly_close_2026-05-14")
    sync = StateSync(_FakeR2(contenido=None), str(local))

    assert sync.pull() is None

    filas = (
        sqlite3.connect(local)
        .execute("SELECT event_id FROM published_events")
        .fetchall()
    )
    assert filas == [("weekly_close_2026-05-14",)]


def test_pull_no_deja_el_local_corrupto_si_la_bajada_viene_rota(tmp_path):
    """Una descarga a medias no puede pisar un estado local sano.

    Por eso baja a un temporal y recien despues reemplaza. Sin eso, un corte
    en mitad de la escritura dejaria la base de Simon ilegible.
    """
    local = tmp_path / "state.db"
    _base_valida(local, event_id="weekly_close_2026-05-14")
    sync = StateSync(_FakeR2(contenido=b"esto no es un sqlite"), str(local))

    motivo = sync.pull()

    assert motivo is not None
    filas = (
        sqlite3.connect(local)
        .execute("SELECT event_id FROM published_events")
        .fetchall()
    )
    assert filas == [("weekly_close_2026-05-14",)]


def test_pull_no_deja_temporales(tmp_path):
    remoto = _base_valida(tmp_path / "remoto.db")
    local = tmp_path / "state.db"
    StateSync(_FakeR2(contenido=remoto), str(local)).pull()

    assert [p.name for p in local.parent.iterdir() if p.name != "remoto.db"] == [
        "state.db"
    ]


# ── push: si levanta ─────────────────────────────────────────────────────────


def test_push_sube_el_fichero(tmp_path):
    local = tmp_path / "state.db"
    esperado = _base_valida(local)
    r2 = _FakeR2()
    sync = StateSync(r2, str(local))

    sync.push()

    assert len(r2.subidas) == 1
    key, cuerpo = r2.subidas[0]
    assert key == "state/state.db"
    assert cuerpo == esperado


def test_push_levanta_si_r2_falla(tmp_path):
    """Al reves que el pull: aca hay quien atrape, y seguir mentiria.

    Si el push falla en silencio, la corrida siguiente lee un estado viejo y
    republica lo que ya salio.
    """
    local = tmp_path / "state.db"
    _base_valida(local)
    sync = StateSync(
        _FakeR2(upload_error=R2ClientError("Error subiendo a R2: AccessDenied")),
        str(local),
    )

    with pytest.raises(StateSyncError):
        sync.push()


def test_push_sin_fichero_local_no_sube_nada(tmp_path):
    """Sin base local no hay nada que sincronizar, y subir vacio la borraria."""
    r2 = _FakeR2()
    sync = StateSync(r2, str(tmp_path / "no-existe.db"))

    sync.push()

    assert r2.subidas == []
