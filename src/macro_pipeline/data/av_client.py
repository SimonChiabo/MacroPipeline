import os
from typing import Any

import pandas as pd
import requests
import structlog
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = structlog.get_logger(__name__)


class AlphaVantageClientError(Exception):
    """Excepción personalizada para errores del cliente Alpha Vantage."""

    pass


class AlphaVantageClient:
    """
    Cliente para interactuar con la API de Alpha Vantage.
    Implementa retries automáticos y parseo a Pandas DataFrames,
    incluyendo manejo específico para los Rate Limits de la capa gratuita.
    """

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Se requiere ALPHA_VANTAGE_API_KEY en variables de entorno o "
                "como argumento."
            )

        self.session = self._configure_session()

    def _configure_session(self) -> requests.Session:
        """Configura una sesión de requests con reintentos para mayor resiliencia."""
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_daily_prices(
        self, symbol: str, outputsize: str = "compact", **kwargs: Any
    ) -> pd.DataFrame:
        """
        Obtiene los precios históricos diarios.

        outputsize puede ser 'compact' (100 días) o 'full'.
        """
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": outputsize,
            "apikey": self.api_key,
            **kwargs,
        }

        logger.info("fetching_av_daily_prices", symbol=symbol)

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("av_api_request_failed", symbol=symbol, error=str(e))
            raise AlphaVantageClientError(f"Error HTTP en Alpha Vantage: {e}") from e

        data = response.json()

        # Alpha Vantage siempre devuelve 200 OK, incluso para errores o rate limits
        if "Error Message" in data:
            msg = data["Error Message"]
            logger.error("av_api_error_message", symbol=symbol, message=msg)
            raise AlphaVantageClientError(msg)

        if "Information" in data and "rate limit" in data["Information"].lower():
            logger.error("av_api_rate_limit", symbol=symbol)
            raise AlphaVantageClientError(
                "Rate limit excedido en Alpha Vantage (máximo llamadas/min o /día)."
            )

        time_series_key = "Time Series (Daily)"
        if time_series_key not in data:
            logger.warning("av_daily_prices_no_data", symbol=symbol)
            return pd.DataFrame()

        # Transformación de datos
        ts_data = data[time_series_key]
        records = []
        for date_str, metrics in ts_data.items():
            records.append(
                {
                    "date": date_str,
                    "open": metrics.get("1. open"),
                    "high": metrics.get("2. high"),
                    "low": metrics.get("3. low"),
                    "close": metrics.get("4. close"),
                    "volume": metrics.get("5. volume"),
                }
            )

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("date").reset_index(drop=True)

        logger.info("av_daily_prices_success", symbol=symbol, records=len(df))
        return df
