"""
Integration tests for Google auth service.
Tests credential refresh against real Google APIs.
"""

import pytest

from app.services.google_auth import (
    get_google_credentials,
    _load_refresh_token_from_db,
    _save_refresh_token_to_db,
    _try_refresh,
    SCOPES,
)
from app.base.config import google_settings


class TestRefreshToken:
    def test_env_refresh_token_is_valid(self):
        if not google_settings.GOOGLE_REFRESH_TOKEN:
            pytest.skip("No GOOGLE_REFRESH_TOKEN in env")
        creds = _try_refresh(google_settings.GOOGLE_REFRESH_TOKEN)
        assert creds is not None
        assert creds.valid

    def test_invalid_refresh_token_returns_none(self):
        result = _try_refresh("invalid-token")
        assert result is None


class TestDbStorage:
    def test_save_and_load(self):
        _save_refresh_token_to_db("test-token-roundtrip")
        loaded = _load_refresh_token_from_db()
        assert loaded == "test-token-roundtrip"

    def test_overwrite_existing(self):
        _save_refresh_token_to_db("first")
        _save_refresh_token_to_db("second")
        loaded = _load_refresh_token_from_db()
        assert loaded == "second"


class TestGetGoogleCredentials:
    def test_returns_valid_credentials(self):
        creds = get_google_credentials()
        assert creds is not None
        assert creds.valid
        assert creds.token is not None
