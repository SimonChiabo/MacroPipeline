from datetime import date, datetime

import pandas as pd
import pytest

from macro_pipeline.data.fred_client import FREDClientError
from macro_pipeline.data.macro import (
    CPI_SERIES,
    DGS10_SERIES,
    UNRATE_SERIES,
    MacroDataError,
    build_macro_snapshot,
    compute_yoy,
    safe_build_macro_snapshot,
)


def _series(pairs):
    """Construye un DataFrame con la forma que devuelve FREDClient."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime([p[0] for p in pairs]),
            "value": [float(p[1]) for p in pairs],
        }
    )


class FakeFred:
    """Doble de FREDClient que registra los kwargs de cada llamada."""

    def __init__(self, series):
        self.series = series
        self.calls = {}

    def get_series_observations(self, series_id, **kwargs):
        self.calls[series_id] = kwargs
        return self.series[series_id]


@pytest.fixture
def fake_fred():
    return FakeFred(
        {
            CPI_SERIES: _series(
                [
                    ("2025-06-01", 300.0),
                    ("2025-12-01", 305.0),
                    ("2026-06-01", 309.0),
                ]
            ),
            UNRATE_SERIES: _series(
                [
                    ("2026-06-01", 4.2),
                    ("2026-07-01", 4.1),
                ]
            ),
            DGS10_SERIES: _series(
                [
                    ("2026-08-05", 4.63),
                    ("2026-08-06", 4.69),
                ]
            ),
        }
    )


def test_compute_yoy_compares_against_observation_12_months_earlier():
    df = _series([("2025-06-01", 300.0), ("2025-12-01", 305.0), ("2026-06-01", 309.0)])

    yoy, as_of = compute_yoy(df)

    assert as_of == date(2026, 6, 1)
    assert yoy == pytest.approx(0.03)  # 309/300 - 1, no contra el valor de diciembre


def test_compute_yoy_raises_when_history_shorter_than_a_year():
    df = _series([("2026-01-01", 300.0), ("2026-06-01", 309.0)])

    with pytest.raises(MacroDataError, match="12 meses"):
        compute_yoy(df)


def test_compute_yoy_raises_on_empty_series():
    with pytest.raises(MacroDataError):
        compute_yoy(_series([]))


def test_build_macro_snapshot_maps_each_series_to_its_field(fake_fred):
    snap = build_macro_snapshot(fake_fred, today=date(2026, 8, 9))

    assert snap.cpi_yoy == pytest.approx(0.03)
    assert snap.cpi_as_of == date(2026, 6, 1)
    assert snap.unemployment_rate == pytest.approx(4.1)
    assert snap.unrate_as_of == date(2026, 7, 1)
    assert snap.treasury_10y == pytest.approx(4.69)
    assert snap.dgs10_as_of == date(2026, 8, 6)


def test_build_macro_snapshot_requests_window_relative_to_today(fake_fred):
    """observation_start sale de today: hardcodearlo rompe el YoY con el tiempo."""
    today = date(2026, 8, 9)

    build_macro_snapshot(fake_fred, today=today)

    start = fake_fred.calls[CPI_SERIES]["observation_start"]
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    # 13 meses (~395 días) no alcanza: el IPC se publica con ~2 meses de retraso,
    # así que la observación base interanual queda justo en el borde de la
    # ventana y el bloque macro desaparecería sin ruido. Se exige holgura real.
    assert (today - start_date).days >= 450


def test_safe_build_macro_snapshot_returns_none_when_fred_fails():
    """El bloque macro es complementario: si FRED falla, el pipeline sigue sin él."""

    class BoomFred:
        def get_series_observations(self, series_id, **kwargs):
            raise FREDClientError("FRED caído")

    assert safe_build_macro_snapshot(BoomFred(), today=date(2026, 8, 9)) is None


def test_safe_build_macro_snapshot_returns_none_on_insufficient_history():
    short = FakeFred(
        {
            CPI_SERIES: _series([("2026-06-01", 309.0)]),
            UNRATE_SERIES: _series([("2026-07-01", 4.1)]),
            DGS10_SERIES: _series([("2026-08-06", 4.69)]),
        }
    )

    assert safe_build_macro_snapshot(short, today=date(2026, 8, 9)) is None


def test_safe_build_macro_snapshot_returns_snapshot_on_success(fake_fred):
    snap = safe_build_macro_snapshot(fake_fred, today=date(2026, 8, 9))

    assert snap is not None
    assert snap.unemployment_rate == pytest.approx(4.1)
