import os
from unittest.mock import MagicMock, patch

import pytest

from macro_pipeline.publishers.linkedin_client import (
    LinkedInClient,
)
from macro_pipeline.publishers.x_client import XClient

# --- Tests para XClient ---


@pytest.fixture
def x_env():
    return {
        "X_API_KEY": "key",
        "X_API_SECRET": "secret",
        "X_ACCESS_TOKEN": "token",
        "X_ACCESS_SECRET": "token_secret",
    }


def test_x_client_missing_keys():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Faltan credenciales de X API"):
            XClient()


@patch("macro_pipeline.publishers.x_client.OAuth1Session")
def test_x_client_post_tweet_success(mock_oauth, x_env):
    # Setup mock
    mock_session_instance = mock_oauth.return_value
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {"id": "1234567890", "text": "Hello World"}
    }
    mock_response.raise_for_status.return_value = None
    mock_session_instance.post.return_value = mock_response

    with patch.dict(os.environ, x_env):
        client = XClient()
        result = client.post_tweet("Hello World")

    assert result["data"]["id"] == "1234567890"
    mock_session_instance.post.assert_called_once()


# --- Tests para LinkedInClient ---


@pytest.fixture
def li_env():
    return {
        "LINKEDIN_ACCESS_TOKEN": "token",
        "LINKEDIN_PERSON_URN": "urn:li:person:123",
    }


def test_linkedin_client_missing_keys():
    with patch.dict(os.environ, clear=True):
        with pytest.raises(ValueError, match="Faltan credenciales de LinkedIn"):
            LinkedInClient()


@patch("requests.Session.post")
def test_linkedin_client_post_text_success(mock_post, li_env):
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "urn:li:share:12345"}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch.dict(os.environ, li_env):
        client = LinkedInClient()
        result = client.post_text("Hello LinkedIn")

    assert result["id"] == "urn:li:share:12345"
    mock_post.assert_called_once()
