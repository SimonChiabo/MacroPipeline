import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from macro_pipeline.data.fmp_client import FMPClient, FMPClientError

@pytest.fixture
def fmp_client():
    return FMPClient(api_key="test_api_key")

def test_fmp_client_init_missing_key():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Se requiere FMP_API_KEY"):
            FMPClient()

@patch('requests.Session.get')
def test_get_historical_prices_success(mock_get, fmp_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "symbol": "AAPL",
        "historical": [
            {
                "date": "2023-01-02",
                "open": 130.0,
                "high": 131.0,
                "low": 129.0,
                "close": 130.5,
                "volume": 10000,
                "adjClose": 130.5
            },
            {
                "date": "2023-01-03",
                "open": 131.0,
                "high": 132.0,
                "low": 130.0,
                "close": 131.5,
                "volume": 12000,
                "adjClose": 131.5
            }
        ]
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    df = fmp_client.get_historical_prices("AAPL")

    mock_get.assert_called_once()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    
    # Validar tipos
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert list(df.columns) == ['date', 'open', 'high', 'low', 'close', 'volume']
    assert df.iloc[0]['close'] == 130.5

@patch('requests.Session.get')
def test_get_historical_prices_empty(mock_get, fmp_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"historical": []}
    mock_get.return_value = mock_response

    df = fmp_client.get_historical_prices("INVALID")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0

@patch('requests.Session.get')
def test_get_earnings_calendar_success(mock_get, fmp_client):
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"date": "2023-02-02", "symbol": "AAPL", "eps": 1.88, "epsEstimated": 1.94},
        {"date": "2023-02-02", "symbol": "AMZN", "eps": 0.03, "epsEstimated": 0.17}
    ]
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    df = fmp_client.get_earnings_calendar("2023-02-01", "2023-02-05")

    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df['date'])
    assert df.iloc[0]['symbol'] == "AAPL"
