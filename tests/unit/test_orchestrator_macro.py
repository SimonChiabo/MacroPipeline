"""Las tres causas por las que el bloque macro no llega, y cual es un fallo.

`_fetch_macro_snapshot` devolvia `None` para las tres sin distinguirlas, y las
tres solo loggeaban. Dos son fallos y una es una configuracion: ADR-009 declara
opcional al bloque macro, y un opcional sin configurar no participa — no
degrada. Avisar todas las semanas de una configuracion permanente es el ruido
que hace que se deje de leer el aviso que importa.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from macro_pipeline.orchestration.main import MacroOrchestrator
from macro_pipeline.validators.engine import ValidationError
from macro_pipeline.validators.schemas import MacroSnapshot


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


def _orchestrator(fred):
    """Solo las dos piezas que `_fetch_macro_snapshot` toca."""
    orch = MacroOrchestrator.__new__(MacroOrchestrator)
    orch.fred = fred
    orch.macro_error = None
    # Sin R2 no hay sincronizado: el pipeline corre contra el disco local,
    # que es como corrian estos tests antes de que el estado viajara.
    orch.state_sync = None
    orch.state_sync_error = None
    orch.validator_engine = MagicMock()
    return orch


def test_fred_without_a_key_is_not_a_failure():
    """Sin key no hay motivo: es una configuracion, no algo que se rompio."""
    orch = _orchestrator(None)

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error is None


def test_a_dead_fred_is_a_failure_and_carries_the_reason(monkeypatch):
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error == "FREDClientError: FRED caído"


def test_a_rejected_figure_is_a_failure_and_says_so(monkeypatch, snapshot):
    """La mas alarmante de las tres y hoy la mas silenciosa.

    Que el validador rechace significa que FRED devolvio una cifra fuera de
    rango de plausibilidad, que es la clase de dato que no se quiere cerca de
    una publicacion financiera.
    """
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    orch.validator_engine.validate_macro_snapshot.side_effect = ValidationError(
        "cpi_yoy=0.9 fuera de rango"
    )

    assert orch._fetch_macro_snapshot() is None
    assert orch.macro_error is not None
    assert "validador" in orch.macro_error.lower()
    assert "cpi_yoy=0.9 fuera de rango" in orch.macro_error


def test_the_reason_distinguishes_the_two_failures(monkeypatch, snapshot):
    """Un aviso que no distingue las dos causas manda a mirar donde no es."""
    caido = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )
    caido._fetch_macro_snapshot()

    rechazado = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    rechazado.validator_engine.validate_macro_snapshot.side_effect = ValidationError(
        "cpi_yoy=0.9 fuera de rango"
    )
    rechazado._fetch_macro_snapshot()

    assert caido.macro_error != rechazado.macro_error


def test_the_happy_path_leaves_no_reason(monkeypatch, snapshot):
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )

    assert orch._fetch_macro_snapshot() is snapshot
    assert orch.macro_error is None


def test_a_retry_does_not_inherit_the_previous_reason(monkeypatch, snapshot):
    """Sin limpiar, una run que reintenta avisa de algo ya resuelto."""
    orch = _orchestrator(MagicMock())
    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (None, "FREDClientError: FRED caído"),
    )
    orch._fetch_macro_snapshot()
    assert orch.macro_error is not None

    monkeypatch.setattr(
        "macro_pipeline.orchestration.main.safe_build_macro_snapshot",
        lambda fred: (snapshot, None),
    )
    orch._fetch_macro_snapshot()

    assert orch.macro_error is None
