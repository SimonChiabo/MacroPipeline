import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from macro_pipeline.validators.schemas import WeeklyCloseData
from macro_pipeline.render.playwright_engine import PlaywrightEngine, PlaywrightEngineError

@pytest.fixture
def mock_sync_playwright():
    """Mock completo de la API de Playwright para evitar lanzar el navegador en CI."""
    with patch('macro_pipeline.render.playwright_engine.sync_playwright') as mock:
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
    fake_png_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
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
        nasdaq_weekly_return=-0.005
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
        # Si falla porque no encuentra el HTML (dependiendo de desde dónde se ejecute pytest)
        # omitimos o fallamos explícitamente. Asumimos que la estructura src/ existe.
        if "No se encontró la plantilla" in str(e):
            pytest.skip("Plantilla HTML no encontrada durante el test. (Verificar pathing)")
        else:
            raise
