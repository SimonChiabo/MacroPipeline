import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import structlog
from typing import Optional, Any

logger = structlog.get_logger(__name__)

class FMPClientError(Exception):
    """Excepción personalizada para errores del cliente FMP."""
    pass

class FMPClient:
    """
    Cliente para interactuar con la API de Financial Modeling Prep.
    Implementa retries automáticos y parseo a Pandas DataFrames.
    """
    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key:
            raise ValueError("Se requiere FMP_API_KEY en variables de entorno o como argumento.")
        
        self.session = self._configure_session()

    def _configure_session(self) -> requests.Session:
        """Configura una sesión de requests con reintentos para mayor resiliencia."""
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_historical_prices(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        """
        Obtiene los precios históricos diarios para un símbolo dado.
        """
        endpoint = f"{self.BASE_URL}/historical-price-full/{symbol}"
        params = {"apikey": self.api_key, **kwargs}
        
        logger.info("fetching_fmp_historical_prices", symbol=symbol)
        
        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("fmp_api_request_failed", symbol=symbol, error=str(e))
            raise FMPClientError(f"Error al obtener datos de FMP: {e}") from e
            
        data = response.json()
        historical = data.get("historical", [])
        
        if not historical:
            logger.warning("fmp_historical_prices_no_data", symbol=symbol)
            return pd.DataFrame()
            
        df = pd.DataFrame(historical)
        df['date'] = pd.to_datetime(df['date'])
        
        # Seleccionamos las columnas clave
        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        df = df[[c for c in cols if c in df.columns]]
        
        # Orden cronológico
        df = df.sort_values("date").reset_index(drop=True)
        
        logger.info("fmp_historical_prices_success", symbol=symbol, records=len(df))
        return df

    def get_earnings_calendar(self, from_date: str, to_date: str, **kwargs: Any) -> pd.DataFrame:
        """
        Obtiene el calendario de earnings (reportes financieros) para un rango de fechas.
        Fechas formato YYYY-MM-DD.
        """
        endpoint = f"{self.BASE_URL}/earning_calendar"
        params = {"apikey": self.api_key, "from": from_date, "to": to_date, **kwargs}
        
        logger.info("fetching_fmp_earnings_calendar", from_date=from_date, to_date=to_date)
        
        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("fmp_api_request_failed", endpoint="earning_calendar", error=str(e))
            raise FMPClientError(f"Error al obtener earnings de FMP: {e}") from e
            
        data = response.json()
        if not data:
            logger.warning("fmp_earnings_no_data", from_date=from_date, to_date=to_date)
            return pd.DataFrame()
            
        df = pd.DataFrame(data)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            
        logger.info("fmp_earnings_success", records=len(df))
        return df
