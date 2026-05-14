import structlog
from pathlib import Path
from playwright.sync_api import sync_playwright

from macro_pipeline.validators.schemas import WeeklyCloseData

logger = structlog.get_logger(__name__)

class PlaywrightEngineError(Exception):
    pass

class PlaywrightEngine:
    """
    Motor de renderizado avanzado usando Playwright.
    Inyecta datos numéricos en plantillas HTML/CSS pre-construidas y toma
    un screenshot exacto y determinista.
    """
    def __init__(self):
        # Asume que las plantillas están en ../templates respecto a este archivo
        self.templates_dir = Path(__file__).parent.parent / "templates"
        self.width = 1080
        self.height = 1080

    def render_weekly_close(self, data: WeeklyCloseData) -> bytes:
        """
        Renderiza el HTML de cierre semanal inyectando los datos de WeeklyCloseData
        y devuelve la imagen en bytes PNG.
        """
        template_path = self.templates_dir / "weekly_close.html"
        if not template_path.exists():
            logger.error("playwright_template_not_found", path=str(template_path))
            raise PlaywrightEngineError(f"No se encontró la plantilla en {template_path}")
            
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
            
        # Determinar clases CSS según si el retorno es positivo o negativo
        sp500_class = "positive" if data.sp500_weekly_return >= 0 else "negative"
        nasdaq_class = "positive" if data.nasdaq_weekly_return >= 0 else "negative"
        
        # Inyección simple de texto usando el formato estándar de Python
        try:
            html_content = template.format(
                date=data.date.strftime("%Y-%m-%d"),
                sp500_close=f"{data.sp500_close:,.2f}",
                sp500_return=f"{data.sp500_weekly_return * 100:+.2f}%",
                sp500_class=sp500_class,
                nasdaq_close=f"{data.nasdaq_close:,.2f}",
                nasdaq_return=f"{data.nasdaq_weekly_return * 100:+.2f}%",
                nasdaq_class=nasdaq_class
            )
        except KeyError as e:
            logger.error("playwright_template_format_error", error=str(e))
            raise PlaywrightEngineError(f"Error al formatear la plantilla HTML: {e}")
        
        logger.info("playwright_rendering_weekly_close", date=data.date.isoformat())
        
        try:
            with sync_playwright() as p:
                # Lanzar en modo headless
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": self.width, "height": self.height})
                
                # Cargar el HTML dinámico directamente en la memoria del browser
                page.set_content(html_content, wait_until="networkidle")
                
                # Tomar screenshot limitando el bounding box (o de la página completa según viewport)
                screenshot_bytes = page.screenshot(type="png", full_page=False)
                browser.close()
                
            logger.info("playwright_render_success", bytes_size=len(screenshot_bytes))
            return screenshot_bytes
            
        except Exception as e:
            logger.error("playwright_render_failed", error=str(e))
            raise PlaywrightEngineError(f"Fallo al renderizar con Playwright: {e}") from e
