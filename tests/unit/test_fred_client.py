import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from macro_pipeline.data.fred_client import FREDClient, FREDClientError

@pytest.fixture
def fred_client():
    """Fixture para inicializar el cliente FRED con un API key ficticio."""
    return FREDClient(api_key="test_api_key")

def test_fred_client_init_missing_key():
    """Prueba que el cliente falla si no hay API key."""
    # Asegurarnos de que no haya API key en el entorno
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere FRED_API_KEY"):
            FREDClient()

@patch('requests.Session.get')
def test_get_series_observations_success(mock_get, fred_client):
    """Prueba la obtención exitosa de observaciones."""
    # Mock de la respuesta de la API
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "observations": [
            {"date": "2023-01-01", "value": "100.5"},
            {"date": "2023-02-01", "value": "101.2"},
            {"date": "2023-03-01", "value": "."}  # Valor nulo en formato FRED
        ]
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    # Llamada al método
    df = fred_client.get_series_observations("TEST_SERIES")

    # Verificaciones
    mock_get.assert_called_once()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2  # El valor '.' debe ser filtrado (dropna)
    
    # Validar tipos de datos y valores
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert pd.api.types.is_numeric_dtype(df['value'])
    assert df.iloc[0]['value'] == 100.5

@patch('requests.Session.get')
def test_get_series_observations_empty(mock_get, fred_client):
    """Prueba el caso donde la API no devuelve observaciones."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"observations": []}
    mock_get.return_value = mock_response

    df = fred_client.get_series_observations("TEST_SERIES")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    assert list(df.columns) == ['date', 'value']

@patch('requests.Session.get')
def test_get_series_observations_error(mock_get, fred_client):
    """Prueba el manejo de errores HTTP en el cliente."""
    import requests
    
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
    mock_get.return_value = mock_response

    with pytest.raises(FREDClientError, match="Error al obtener datos de FRED"):
        fred_client.get_series_observations("INVALID_SERIES")


@patch('requests.Session.get')
def test_parses_recorded_fred_payload(mock_get, fred_client):
    """Parsea una respuesta real grabada de FRED (tests/fixtures/).

    Los otros tests usan dicts escritos a mano con solo 'date' y 'value'. El
    payload real trae además 'realtime_start' y 'realtime_end' en cada
    observación: este test es el que verifica que el cliente las descarta.
    """
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "fred_gdp_observations.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    df = fred_client.get_series_observations("GDP")

    assert list(df.columns) == ['date', 'value']
    assert len(df) == len(payload["observations"])
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert pd.api.types.is_numeric_dtype(df['value'])
    assert df.iloc[0]['value'] == float(payload["observations"][0]["value"])
