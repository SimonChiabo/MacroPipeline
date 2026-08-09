import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from macro_pipeline.data.fmp_client import FMPClient, FMPClientError

@pytest.fixture
def fmp_client():
    return FMPClient(api_key="test_api_key")

def _mock_json(mock_get, payload):
    """Configura la respuesta JSON del mock de sesión."""
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response
    return mock_response

def test_fmp_client_init_missing_key():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere FMP_API_KEY"):
            FMPClient()

@patch('requests.Session.get')
def test_get_historical_prices_success(mock_get, fmp_client):
    """La API stable devuelve una lista plana, más reciente primero."""
    _mock_json(mock_get, [
        {
            "symbol": "^GSPC", "date": "2023-01-03",
            "open": 131.0, "high": 132.0, "low": 130.0, "close": 131.5,
            "volume": 12000, "change": 0.5, "changePercent": 0.38, "vwap": 131.2
        },
        {
            "symbol": "^GSPC", "date": "2023-01-02",
            "open": 130.0, "high": 131.0, "low": 129.0, "close": 130.5,
            "volume": 10000, "change": 0.5, "changePercent": 0.38, "vwap": 130.2
        },
    ])

    df = fmp_client.get_historical_prices("^GSPC")

    mock_get.assert_called_once()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert list(df.columns) == ['date', 'open', 'high', 'low', 'close', 'volume']

    # Orden cronológico ascendente: el input viene invertido desde la API
    assert df.iloc[0]['date'] < df.iloc[-1]['date']
    assert df.iloc[0]['close'] == 130.5
    assert df.iloc[-1]['close'] == 131.5

@patch('requests.Session.get')
def test_get_historical_prices_uses_stable_endpoint(mock_get, fmp_client):
    """El símbolo va como query param, no en el path (API stable, no legacy v3)."""
    _mock_json(mock_get, [
        {"symbol": "^GSPC", "date": "2023-01-02", "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1}
    ])

    fmp_client.get_historical_prices("^GSPC")

    url = mock_get.call_args.args[0]
    params = mock_get.call_args.kwargs["params"]
    assert url == "https://financialmodelingprep.com/stable/historical-price-eod/full"
    assert "/api/v3" not in url
    assert params["symbol"] == "^GSPC"
    assert params["apikey"] == "test_api_key"

@patch('requests.Session.get')
def test_get_historical_prices_empty_raises(mock_get, fmp_client):
    """Lista vacía debe lanzar FMPClientError, no retornar DataFrame vacío silenciosamente."""
    _mock_json(mock_get, [])

    with pytest.raises(FMPClientError, match="respuesta vacía"):
        fmp_client.get_historical_prices("INVALID")

@patch('requests.Session.get')
def test_get_historical_prices_error_payload_raises(mock_get, fmp_client):
    """FMP a veces devuelve 200 con un dict de error: debe fallar limpio, no producir basura."""
    _mock_json(mock_get, {"Error Message": "Invalid API KEY."})

    with pytest.raises(FMPClientError, match="respuesta vacía"):
        fmp_client.get_historical_prices("^GSPC")

@patch('requests.Session.get')
def test_get_earnings_calendar_success(mock_get, fmp_client):
    """Schema stable: epsActual/revenueActual en lugar de eps."""
    _mock_json(mock_get, [
        {"symbol": "AAPL", "date": "2023-02-02", "epsActual": 1.88,
         "epsEstimated": 1.94, "revenueActual": 117154000000, "revenueEstimated": 121100000000},
        {"symbol": "AMZN", "date": "2023-02-02", "epsActual": 0.03,
         "epsEstimated": 0.17, "revenueActual": 149204000000, "revenueEstimated": 145781000000},
    ])

    df = fmp_client.get_earnings_calendar("2023-02-01", "2023-02-05")

    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert df.iloc[0]['symbol'] == "AAPL"
    assert df.iloc[0]['epsActual'] == 1.88

@patch('requests.Session.get')
def test_get_earnings_calendar_uses_stable_endpoint(mock_get, fmp_client):
    _mock_json(mock_get, [{"symbol": "AAPL", "date": "2023-02-02"}])

    fmp_client.get_earnings_calendar("2023-02-01", "2023-02-05")

    url = mock_get.call_args.args[0]
    params = mock_get.call_args.kwargs["params"]
    assert url == "https://financialmodelingprep.com/stable/earnings-calendar"
    assert params["from"] == "2023-02-01"
    assert params["to"] == "2023-02-05"
