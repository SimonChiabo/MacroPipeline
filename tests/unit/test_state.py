"""Tests de StateDB, con foco en la persistencia del contexto macro."""

import sqlite3
from datetime import date

import pytest

from macro_pipeline.storage.state import StateDB
from macro_pipeline.validators.schemas import MacroSnapshot

# Schema anterior a la migración macro: el que tienen las bases ya creadas.
_OLD_SCHEMA = """
    CREATE TABLE published_events (
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
        image_url           TEXT,
        x_post_id           TEXT,
        linkedin_post_id    TEXT
    )
"""


@pytest.fixture
def db(tmp_path):
    """StateDB sobre un archivo temporal, aislado del estado real del usuario."""
    return StateDB(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def snapshot():
    return MacroSnapshot(
        cpi_yoy=0.033,
        cpi_as_of=date(2026, 7, 1),
        unemployment_rate=4.1,
        unrate_as_of=date(2026, 7, 1),
        treasury_10y=4.65,
        dgs10_as_of=date(2026, 8, 20),
    )


def test_mark_as_published_persists_macro_snapshot(db, snapshot):
    """Los seis campos macro sobreviven al guardado y se recuperan igual."""
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", macro=snapshot)

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["cpi_yoy"] == pytest.approx(0.033)
    assert state["cpi_as_of"] == "2026-07-01"
    assert state["unemployment_rate"] == pytest.approx(4.1)
    assert state["unrate_as_of"] == "2026-07-01"
    assert state["treasury_10y"] == pytest.approx(4.65)
    assert state["dgs10_as_of"] == "2026-08-20"


def test_mark_as_published_without_macro_leaves_nulls(db):
    """Sin snapshot macro se guarda igual: es el camino habitual, no el borde.

    `safe_build_macro_snapshot` devuelve None cuando FRED falla, y eso no debe
    impedir que el cierre semanal quede registrado como publicado.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", headline="Cierre semanal")

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["status"] == "published"
    assert state["headline"] == "Cierre semanal"
    for column in (
        "cpi_yoy",
        "cpi_as_of",
        "unemployment_rate",
        "unrate_as_of",
        "treasury_10y",
        "dgs10_as_of",
    ):
        assert state[column] is None


def test_migrate_db_adds_macro_columns_preserving_rows(tmp_path):
    """Una base con el schema viejo gana las columnas macro sin perder datos."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO published_events (event_id, status, created_at, headline) "
            "VALUES ('weekly_close_2026-05-14', 'published', '2026-05-14', 'viejo')"
        )

    db = StateDB(db_path=str(db_path))
    state = db.get_publication_state("weekly_close_2026-05-14")

    assert state["headline"] == "viejo"
    assert state["cpi_yoy"] is None
    assert "dgs10_as_of" in state


def test_second_publish_overwrites_macro_columns(db, snapshot):
    """Semántica elegida: gana la última escritura, también para el macro.

    Test de caracterización: fija la decisión de que la fila refleje la run que
    publicó, igual que ya ocurría con `headline` e `image_url`. En la práctica
    no es alcanzable desde el orquestador —`is_published` corta la re-ejecución
    de un evento ya publicado—, pero si algún día se levanta esa guarda para
    reprocesar, esto documenta que los valores viejos no se preservan.
    """
    db.mark_in_progress("weekly_close_2026-08-21")
    db.mark_as_published("weekly_close_2026-08-21", macro=snapshot)
    db.mark_as_published("weekly_close_2026-08-21", macro=None)

    state = db.get_publication_state("weekly_close_2026-08-21")

    assert state["cpi_yoy"] is None
    assert state["dgs10_as_of"] is None
