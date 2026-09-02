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

    snapshot, motivo = safe_build_macro_snapshot(BoomFred(), today=date(2026, 8, 9))

    assert snapshot is None
    assert motivo is not None
    assert "FRED caído" in motivo, "el motivo tiene que llevar el error real"
    assert "FREDClientError" in motivo, "y el tipo, para saber dónde mirar"


def test_safe_build_macro_snapshot_returns_none_on_insufficient_history():
    short = FakeFred(
        {
            CPI_SERIES: _series([("2026-06-01", 309.0)]),
            UNRATE_SERIES: _series([("2026-07-01", 4.1)]),
            DGS10_SERIES: _series([("2026-08-06", 4.69)]),
        }
    )

    snapshot, motivo = safe_build_macro_snapshot(short, today=date(2026, 8, 9))

    assert snapshot is None
    assert motivo is not None


def test_the_reason_distinguishes_a_dead_fred_from_a_short_series():
    """Dos fallos distintos no pueden dar el mismo aviso.

    Es la leccion de `f53a755`: la alerta de la capa LLM culpaba al prompt
    tambien cuando lo que moria era la API, y mandaba a revisar un prompt sano.
    """

    class BoomFred:
        def get_series_observations(self, series_id, **kwargs):
            raise FREDClientError("FRED caído")

    short = FakeFred(
        {
            CPI_SERIES: _series([("2026-06-01", 309.0)]),
            UNRATE_SERIES: _series([("2026-07-01", 4.1)]),
            DGS10_SERIES: _series([("2026-08-06", 4.69)]),
        }
    )

    _, motivo_caido = safe_build_macro_snapshot(BoomFred(), today=date(2026, 8, 9))
    _, motivo_corto = safe_build_macro_snapshot(short, today=date(2026, 8, 9))

    assert motivo_caido != motivo_corto


def test_safe_build_macro_snapshot_redacts_a_credential_in_the_reason():
    """La URL de FRED lleva la api_key en la query string.

    `str(HTTPError)` la incluye entera, y ese texto es lo que viaja tal cual
    hasta el aviso de Telegram. Si el motivo no se redacta acá, la key queda
    en el historial del chat en texto plano.
    """

    class LeakyFred:
        def get_series_observations(self, series_id, **kwargs):
            raise FREDClientError(
                "Error al obtener datos de FRED: 401 Client Error: "
                "Unauthorized for url: "
                "https://api.stlouisfed.org/fred/series/observations"
                "?series_id=CPIAUCSL&api_key=FAKESECRET1234567890&file_type=json"
            )

    snapshot, motivo = safe_build_macro_snapshot(LeakyFred(), today=date(2026, 8, 9))

    assert snapshot is None
    assert motivo is not None
    assert "FAKESECRET1234567890" not in motivo
    assert "***REDACTED***" in motivo


def test_safe_build_macro_snapshot_returns_snapshot_on_success(fake_fred):
    snap, motivo = safe_build_macro_snapshot(fake_fred, today=date(2026, 8, 9))

    assert snap is not None
    assert snap.unemployment_rate == pytest.approx(4.1)
    assert motivo is None, "el camino feliz no deja motivo cargado"


def test_cpi_is_requested_on_the_not_seasonally_adjusted_series(fake_fred):
    """El interanual del IPC se pide sobre `CPIAUCNS`, la serie del titular.

    La convención es NSA para el interanual y SA para la variación mensual: al
    comparar un mes contra el mismo mes del año anterior el factor estacional
    se cancela solo, así que desestacionalizar no aporta nada y sólo agrega la
    revisión anual de los factores. Con `CPIAUCSL` el post publicaba +3,3 %
    donde el BLS y los medios decían 3,4 % (ver `docs/data-dictionary.md`).

    El assert va sobre la llamada y no sobre `CPI_SERIES` a propósito: una
    aserción sobre la constante se cumple con sólo renombrarla, sin que el ETL
    cambie de serie.
    """
    build_macro_snapshot(fake_fred, today=date(2026, 8, 9))

    assert "CPIAUCNS" in fake_fred.calls
    assert "CPIAUCSL" not in fake_fred.calls, (
        "el ETL sigue pidiendo la serie desestacionalizada"
    )
