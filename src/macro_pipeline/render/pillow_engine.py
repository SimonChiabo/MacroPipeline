import io

import structlog
from PIL import Image, ImageDraw, ImageFont

from macro_pipeline.validators.schemas import EarningsData

logger = structlog.get_logger(__name__)


class PillowEngine:
    """
    Motor de renderizado basado en Pillow para generar imágenes estáticas
    (ej: resúmenes simples, calendarios).
    """

    def __init__(self):
        # Configuración base (Dark Theme estilo Tailwind)
        self.bg_color = (15, 23, 42)  # Slate 900
        self.text_color = (248, 250, 252)  # Slate 50
        self.accent_color = (56, 189, 248)  # Sky 400
        self.width = 1080
        self.height = 1080

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Intenta cargar una fuente estándar o usa la predeterminada."""
        try:
            # Arial suele estar disponible en muchos sistemas, intentar usarla
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            # Fallback seguro
            return ImageFont.load_default()

    def render_earnings_calendar(self, data: EarningsData) -> bytes:
        """
        Genera una imagen 1080x1080 con el calendario de earnings.
        """
        img = Image.new("RGB", (self.width, self.height), self.bg_color)
        draw = ImageDraw.Draw(img)

        # Cargar fuentes (el fallback a default puede hacer que el tamaño no aplique,
        # en producción montaríamos archivos .ttf en el repo o container)
        font_title = self._get_font(60)
        font_header = self._get_font(40)
        font_body = self._get_font(32)

        # Título
        title_text = f"Calendario de Earnings: {data.date.strftime('%Y-%m-%d')}"
        draw.text((60, 60), title_text, font=font_title, fill=self.text_color)
        draw.line([(60, 140), (1020, 140)], fill=self.accent_color, width=4)

        # Cabeceras de tabla
        draw.text((60, 180), "Simbolo", font=font_header, fill=self.accent_color)
        draw.text((360, 180), "EPS Real", font=font_header, fill=self.accent_color)
        draw.text((660, 180), "EPS Est.", font=font_header, fill=self.accent_color)

        # Dibujar reportes
        y_offset = 260
        for report in data.reports[:10]:  # Limitar a 10 para que quepa verticalmente
            draw.text(
                (60, y_offset), report.symbol, font=font_body, fill=self.text_color
            )

            eps_actual = f"${report.eps_actual:.2f}"
            eps_est = f"${report.eps_estimated:.2f}" if report.eps_estimated else "N/A"

            # Color basado en superación de expectativas
            # (verde si supera/iguala, rojo si falla)
            if report.eps_estimated:
                actual_color = (
                    (74, 222, 128)
                    if report.eps_actual >= report.eps_estimated
                    else (248, 113, 113)
                )
            else:
                actual_color = self.text_color

            draw.text((360, y_offset), eps_actual, font=font_body, fill=actual_color)
            draw.text((660, y_offset), eps_est, font=font_body, fill=self.text_color)

            y_offset += 70

        if len(data.reports) > 10:
            draw.text(
                (60, y_offset),
                f"... y {len(data.reports) - 10} mas",
                font=font_body,
                fill=self.text_color,
            )

        logger.info("pillow_earnings_rendered", records=len(data.reports))
        return self._image_to_bytes(img)

    def _image_to_bytes(self, img: Image.Image) -> bytes:
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        return img_byte_arr.getvalue()
