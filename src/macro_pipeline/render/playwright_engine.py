from pathlib import Path

import structlog
from playwright.sync_api import sync_playwright

from macro_pipeline.validators.schemas import MacroSnapshot, WeeklyCloseData

logger = structlog.get_logger(__name__)


class PlaywrightEngineError(Exception):
    pass


class PlaywrightEngine:
    """
    Motor de renderizado avanzado usando Playwright.
    Inyecta datos numéricos en plantillas HTML/CSS pre-construidas y toma
    un screenshot exacto y determinista.
    """

    def __init__(self) -> None:
        # Asume que las plantillas están en ../templates respecto a este archivo
        self.templates_dir = Path(__file__).parent.parent / "templates"
        self.width = 1080
        self.height = 1080

    def _build_macro_block(self, macro: MacroSnapshot | None) -> str:
        """
        Construye el bloque macro como HTML, o cadena vacía si no hay datos.

        Cada indicador muestra su fecha de referencia: el IPC y el desempleo se
        publican con semanas de retraso y presentarlos sin fecha daría a
        entender que son del día del cierre.
        """
        if macro is None:
            return ""

        # Las series mensuales se fechan por mes; el Treasury es diario y su día
        # importa, porque es lo que cambia entre una publicación y la siguiente.
        items = [
            (
                "IPC interanual",
                f"{macro.cpi_yoy * 100:+.1f}%",
                macro.cpi_as_of,
                "%m/%Y",
            ),
            (
                "Desempleo",
                f"{macro.unemployment_rate:.1f}%",
                macro.unrate_as_of,
                "%m/%Y",
            ),
            (
                "Treasury 10A",
                f"{macro.treasury_10y:.2f}%",
                macro.dgs10_as_of,
                "%d/%m/%Y",
            ),
        ]
        cells = "".join(
            '<div class="macro-item">'
            f'<span class="macro-label">{label}</span>'
            f'<span class="macro-value">{value}</span>'
            f'<span class="macro-asof">al {as_of.strftime(date_format)}</span>'
            "</div>"
            for label, value, as_of, date_format in items
        )
        return f'<div class="macro-strip">{cells}</div>'

    def render_weekly_close(self, data: WeeklyCloseData) -> bytes:
        """
        Renderiza el HTML de cierre semanal inyectando los datos de WeeklyCloseData
        y devuelve la imagen en bytes PNG.
        """
        template_path = self.templates_dir / "weekly_close.html"
        if not template_path.exists():
            logger.error("playwright_template_not_found", path=str(template_path))
            raise PlaywrightEngineError(
                f"No se encontró la plantilla en {template_path}"
            )

        with open(template_path, encoding="utf-8") as f:
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
                nasdaq_class=nasdaq_class,
                macro_block=self._build_macro_block(data.macro),
            )
        except KeyError as e:
            logger.error("playwright_template_format_error", error=str(e))
            raise PlaywrightEngineError(
                f"Error al formatear la plantilla HTML: {e}"
            ) from e

        logger.info("playwright_rendering_weekly_close", date=data.date.isoformat())

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(
                        viewport={"width": self.width, "height": self.height}
                    )
                    # timeout=15000 evita espera infinita si hay recursos externos
                    page.set_content(
                        html_content, wait_until="networkidle", timeout=15000
                    )
                    screenshot_bytes = page.screenshot(type="png", full_page=False)
                finally:
                    # Garantiza cleanup aunque page.screenshot() lance excepción
                    browser.close()

            logger.info("playwright_render_success", bytes_size=len(screenshot_bytes))
            return screenshot_bytes

        except Exception as e:
            logger.error("playwright_render_failed", error=str(e))
            raise PlaywrightEngineError(
                f"Fallo al renderizar con Playwright: {e}"
            ) from e
