import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from macro_pipeline.data.av_client import AlphaVantageClient, AlphaVantageClientError

@pytest.fixture
def av_client():
    return AlphaVantageClient(api_key="test_api_key")

def test_av_client_init_missing_key():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere ALPHA_VANTAGE_API_KEY"):
            AlphaVantageClient()

@patch('requests.Session.get')
def test_get_daily_prices_success(mock_get, av_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "Meta Data": {
            "1. Information": "Daily Prices (open, high, low, close) and Volumes",
            "2. Symbol": "IBM"
        },
        "Time Series (Daily)": {
            "2023-01-03": {
                "1. open": "141.1000",
                "2. high": "141.2000",
                "3. low": "140.0000",
                "4. close": "140.5000",
                "5. volume": "5000000"
            },
            "2023-01-02": {
                "1. open": "140.0000",
                "2. high": "141.0000",
                "3. low": "139.5000",
                "4. close": "140.0000",
                "5. volume": "4500000"
            }
        }
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    df = av_client.get_daily_prices("IBM")

    assert len(df) == 2
    # El sort_values("date") debe ordenar 01-02 antes que 01-03
    assert df.iloc[0]['date'] == pd.Timestamp("2023-01-02")
    assert df.iloc[0]['close'] == 140.0000
    assert df.iloc[1]['close'] == 140.5000

@patch('requests.Session.get')
def test_get_daily_prices_rate_limit(mock_get, av_client):
    mock_response = MagicMock()
    # Alpha Vantage devuelve 200 OK pero con una key de Information
    mock_response.json.return_value = {
        "Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
    }
    mock_get.return_value = mock_response

    with pytest.raises(AlphaVantageClientError, match="Rate limit excedido"):
        av_client.get_daily_prices("IBM")

@patch('requests.Session.get')
def test_get_daily_prices_error_message(mock_get, av_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "Error Message": "Invalid API call. Please retry or visit the documentation."
    }
    mock_get.return_value = mock_response

    with pytest.raises(AlphaVantageClientError, match="Invalid API call"):
        av_client.get_daily_prices("INVALID")
