import os
from typing import Any

import pandas as pd
import requests
import structlog
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = structlog.get_logger(__name__)


class FREDClientError(Exception):
    """Excepción personalizada para errores del cliente FRED."""

    pass


class FREDClient:
    """
    Cliente para interactuar con la API de FRED (Federal Reserve Economic Data).
    Implementa retries automáticos y parseo a Pandas DataFrames.
    """

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FRED_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Se requiere FRED_API_KEY en variables de entorno o como argumento."
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

    def get_series_observations(self, series_id: str, **kwargs: Any) -> pd.DataFrame:
        """
        Obtiene las observaciones de una serie específica de FRED.

        Args:
            series_id: El ID de la serie de FRED (ej. 'GDP', 'UNRATE', 'CPIAUCSL').
            **kwargs: Parámetros adicionales para la API
                (observation_start, frequency, etc).

        Returns:
            Un Pandas DataFrame con las columnas 'date' y 'value'.
        """
        endpoint = f"{self.BASE_URL}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            **kwargs,
        }

        logger.info("fetching_fred_series", series_id=series_id)

        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("fred_api_request_failed", series_id=series_id, error=str(e))
            raise FREDClientError(f"Error al obtener datos de FRED: {e}") from e

        data = response.json()
        observations = data.get("observations", [])

        if not observations:
            logger.warning("fred_series_no_data", series_id=series_id)
            return pd.DataFrame(columns=["date", "value"])

        df = pd.DataFrame(observations)

        # Limpieza determinista de datos
        # FRED puede devolver '.' para valores nulos
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")

        # Filtramos nulos y columnas irrelevantes (realtime_start, realtime_end)
        df = df[["date", "value"]].dropna().reset_index(drop=True)

        logger.info("fred_series_success", series_id=series_id, records=len(df))
        return df
