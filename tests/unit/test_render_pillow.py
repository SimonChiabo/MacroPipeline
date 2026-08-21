from datetime import date
from io import BytesIO

from PIL import Image

from macro_pipeline.render.pillow_engine import PillowEngine
from macro_pipeline.validators.schemas import EarningReport, EarningsData


def test_pillow_render_earnings():
    """Prueba que el motor de Pillow genere una imagen PNG válida."""
    engine = PillowEngine()

    # Creamos datos mock usando los esquemas Pydantic ya testeados
    data = EarningsData(
        date=date(2023, 1, 1),
        reports=[
            EarningReport(symbol="AAPL", eps_actual=1.50, eps_estimated=1.40),  # Supera
            EarningReport(symbol="MSFT", eps_actual=2.10, eps_estimated=2.20),  # Falla
            EarningReport(symbol="GOOGL", eps_actual=1.00, eps_estimated=None),  # N/A
        ],
    )

    img_bytes = engine.render_earnings_calendar(data)

    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 0

    # Verificamos que los bytes correspondan a una imagen PNG de 1080x1080
    img = Image.open(BytesIO(img_bytes))
    assert img.format == "PNG"
    assert img.size == (1080, 1080)
