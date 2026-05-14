import yaml
import structlog
from pathlib import Path
from typing import Any, Dict

from macro_pipeline.validators.schemas import WeeklyCloseData, MacroReleaseData, EarningsData

logger = structlog.get_logger(__name__)

class ValidationError(Exception):
    """Excepción lanzada cuando los datos no superan los sanity checks numéricos."""
    pass

class ValidationEngine:
    """
    Motor que aplica las reglas declarativas (YAML) a los esquemas validados de Pydantic.
    Actúa como la última línea de defensa antes de la renderización.
    """
    def __init__(self, rules_path: str = None):
        if not rules_path:
            # Default to the rules.yaml in the same directory
            rules_path = str(Path(__file__).parent / "rules.yaml")
        self.rules = self._load_rules(rules_path)

    def _load_rules(self, path: str) -> Dict[str, Any]:
        """Carga las reglas declarativas desde el archivo YAML."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("failed_loading_rules", path=path, error=str(e))
            raise

    def validate_weekly_close(self, data: WeeklyCloseData) -> bool:
        """
        Aplica los sanity checks para los datos del cierre semanal.
        Lanza ValidationError si falla.
        """
        rules = self.rules.get("weekly_close", {})
        
        # Validar retornos del SP500
        sp500_min = rules.get("sp500_return_min", -1.0)
        sp500_max = rules.get("sp500_return_max", 1.0)
        
        if not (sp500_min <= data.sp500_weekly_return <= sp500_max):
            logger.error("validation_failed", 
                         reason="sp500_weekly_return_out_of_bounds", 
                         value=data.sp500_weekly_return)
            raise ValidationError(f"Retorno del SP500 {data.sp500_weekly_return} fuera del rango permitido.")
            
        # Validar retornos del NASDAQ
        nasdaq_min = rules.get("nasdaq_return_min", -1.0)
        nasdaq_max = rules.get("nasdaq_return_max", 1.0)
        
        if not (nasdaq_min <= data.nasdaq_weekly_return <= nasdaq_max):
            logger.error("validation_failed", 
                         reason="nasdaq_weekly_return_out_of_bounds", 
                         value=data.nasdaq_weekly_return)
            raise ValidationError(f"Retorno del NASDAQ {data.nasdaq_weekly_return} fuera del rango permitido.")
            
        logger.info("weekly_close_validated", date=data.date.isoformat())
        return True

    def validate_macro_release(self, data: MacroReleaseData) -> bool:
        """
        Aplica los sanity checks para indicadores macroeconómicos.
        """
        rules = self.rules.get("macro_release", {})
        
        # Ejemplo: Control específico para GDP
        if data.indicator_name.upper() == "GDP":
            gdp_max_abs = rules.get("gdp_growth_max_abs", 0.5)
            if abs(data.actual) > gdp_max_abs:
                logger.error("validation_failed", reason="gdp_growth_anomaly", value=data.actual)
                raise ValidationError(f"Cambio en GDP del {data.actual} parece poco realista o erróneo.")
                
        # Ejemplo: Control específico para Desempleo
        if data.indicator_name.upper() == "UNRATE":
            unrate_max = rules.get("unrate_max", 100.0)
            unrate_min = rules.get("unrate_min", 0.0)
            if not (unrate_min <= data.actual <= unrate_max):
                logger.error("validation_failed", reason="unrate_anomaly", value=data.actual)
                raise ValidationError(f"Tasa de desempleo {data.actual} fuera de rangos racionales.")

        logger.info("macro_release_validated", indicator=data.indicator_name, date=data.date.isoformat())
        return True
