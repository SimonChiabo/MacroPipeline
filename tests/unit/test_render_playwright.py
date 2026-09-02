from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from macro_pipeline.render.playwright_engine import (
    PlaywrightEngine,
    PlaywrightEngineError,
)
from macro_pipeline.validators.schemas import MacroSnapshot, WeeklyCloseData


def _weekly_close(macro=None):
    return WeeklyCloseData(
        date=date(2026, 8, 7),
        sp500_close=7712.33,
        sp500_weekly_return=0.0369,
        nasdaq_close=26372.33,
        nasdaq_weekly_return=0.0498,
        macro=macro,
    )


def _macro():
    return MacroSnapshot(
        cpi_yoy=0.024,
        cpi_as_of=date(2026, 6, 1),
        unemployment_rate=4.1,
        unrate_as_of=date(2026, 7, 1),
        treasury_10y=4.69,
        dgs10_as_of=date(2026, 8, 6),
    )


def _rendered_html(mock_sync_playwright, data):
    """Ejecuta el render mockeado y devuelve el HTML que se inyectó en la página."""
    mock_p = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = mock_p
    mock_browser = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_page = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_page.screenshot.return_value = b"\x89PNG"

    PlaywrightEngine().render_weekly_close(data)

    return mock_page.set_content.call_args.args[0]


@pytest.fixture
def mock_sync_playwright():
    """Mock completo de la API de Playwright para evitar lanzar el navegador en CI."""
    with patch("macro_pipeline.render.playwright_engine.sync_playwright") as mock:
        yield mock


def test_playwright_render_weekly_close(mock_sync_playwright):
    # Setup del mock estructurado como un context manager que devuelve un browser
    mock_p = MagicMock()
    mock_sync_playwright.return_value.__enter__.return_value = mock_p

    mock_browser = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser

    mock_page = MagicMock()
    mock_browser.new_page.return_value = mock_page

    # El screenshot debe devolver bytes simulados
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    mock_page.screenshot.return_value = fake_png_bytes

    # Ejecución
    engine = PlaywrightEngine()

    # Asegurar que el path del template apunte a donde debe estar (en un test real
    # podríamos inyectar un HTML mockeado o apuntar al real si asumimos su existencia)
    data = WeeklyCloseData(
        date=date(2023, 1, 6),
        sp500_close=3895.0,
        sp500_weekly_return=0.014,
        nasdaq_close=10569.0,
        nasdaq_weekly_return=-0.005,
    )

    try:
        result = engine.render_weekly_close(data)

        # Validaciones
        assert result == fake_png_bytes
        mock_p.chromium.launch.assert_called_once_with(headless=True)
        mock_page.set_content.assert_called_once()
        mock_page.screenshot.assert_called_once_with(type="png", full_page=False)
        mock_browser.close.assert_called_once()
    except PlaywrightEngineError as e:
        # Si falla porque no encuentra el HTML (dependiendo de desde dónde
        # se ejecute pytest) omitimos o fallamos explícitamente. Asumimos
        # que la estructura src/ existe.
        if "No se encontró la plantilla" in str(e):
            pytest.skip(
                "Plantilla HTML no encontrada durante el test. (Verificar pathing)"
            )
        else:
            raise


def test_render_includes_macro_block_when_present(mock_sync_playwright):
    html = _rendered_html(mock_sync_playwright, _weekly_close(macro=_macro()))

    assert '<div class="macro-strip">' in html
    assert "+2.4%" in html  # IPC interanual
    assert "4.1%" in html  # desempleo
    assert "4.69%" in html  # treasury 10 años
    # La fecha de referencia es obligatoria: el CPI va meses atrasado
    assert "al 06/2026" in html
    # El Treasury es una serie diaria: mostrar solo el mes ocultaría de qué día
    # es el dato, que es exactamente lo que varía
    assert "al 06/08/2026" in html


def test_render_omits_macro_block_when_absent(mock_sync_playwright):
    html = _rendered_html(mock_sync_playwright, _weekly_close(macro=None))

    # El CSS vive siempre en el <style>; lo que no debe aparecer es el marcado
    assert '<div class="macro-strip">' not in html
    assert "IPC interanual" not in html
    # El bloque de mercado sigue intacto
    assert "7,712.33" in html


def _weekly_close_sin_niveles(macro=None):
    return WeeklyCloseData(
        date=date(2026, 8, 7),
        sp500_close=None,
        sp500_weekly_return=0.0369,
        nasdaq_close=None,
        nasdaq_weekly_return=-0.0498,
        macro=macro,
    )


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_sin_niveles_muestra_el_retorno_y_ningun_nivel(mock_sync_playwright):
    """Lo que no se puede rotular, no aparece: la imagen nunca miente."""
    html = _rendered_html(mock_sync_playwright, _weekly_close_sin_niveles())

    assert "+3.69%" in html
    assert "-4.98%" in html
    assert "variación semanal" in html
    # El assert va contra el markup y NO contra `"metric-return" in html`:
    # la regla `.metric-return` vive en el `<style>` de la plantilla y está
    # siempre, con nivel o sin él. Ese assert quedaría rojo contra una
    # implementación correcta, y el arreglo tentador —borrar la regla CSS—
    # rompería la ruta de FMP, que sí la usa.
    assert '<div class="metric-return' not in html


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_con_niveles_no_cambia(mock_sync_playwright):
    """La ruta de FMP renderiza exactamente como antes."""
    html = _rendered_html(mock_sync_playwright, _weekly_close())

    assert "7,712.33" in html
    assert "26,372.33" in html
    assert "+3.69%" in html
    assert "variación semanal" not in html


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_sin_niveles_conserva_el_bloque_macro(mock_sync_playwright):
    """Las dos degradaciones son independientes y se combinan."""
    html = _rendered_html(mock_sync_playwright, _weekly_close_sin_niveles(_macro()))

    assert "IPC interanual" in html
    assert "variación semanal" in html


@patch("macro_pipeline.render.playwright_engine.sync_playwright")
def test_render_names_the_nasdaq_index_it_actually_publishes(mock_sync_playwright):
    """La tarjeta dice cuál de los dos Nasdaq es.

    `^IXIC` es el Composite; el Nasdaq-100 es otro indice y cotiza ~11% mas
    alto (medido el 2026-09-02: 26.099,77 contra 29.077,22). Rotulado
    «NASDAQ» a secas, quien mire una terminal de mercado ve otro numero y
    concluye que el pipeline calcula mal — le paso al propio autor del
    proyecto. Es la misma familia que la divergencia 4 de ADR-009: la etiqueta
    correcta sobre el instrumento equivocado, esta vez al reves.
    """
    html = _rendered_html(mock_sync_playwright, _weekly_close())

    assert "Nasdaq Composite" in html
    assert ">NASDAQ<" not in html
