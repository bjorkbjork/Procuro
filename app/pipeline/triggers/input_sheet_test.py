"""Tests for the input sheet trigger — polls sheet for unprocessed URLs."""

from unittest.mock import MagicMock

import pytest

from app.base.config import PROJECT_ROOT
from app.pipeline.triggers.input_sheet import get_new_urls
from app.db import database as _db
from app.db.models.source_product import SourceProduct

FAKE_URL_1 = "https://www.kogan.com/au/buy/test-trigger-1/"
FAKE_URL_2 = "https://www.kogan.com/au/buy/test-trigger-2/"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with _db.SessionLocal() as session:
        session.query(SourceProduct).filter(
            SourceProduct.url.in_([FAKE_URL_1, FAKE_URL_2])
        ).delete()
        session.commit()


@pytest.fixture
def mock_sheets(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("app.pipeline.triggers.input_sheet.SheetsService", lambda: mock)
    return mock


class TestGetNewUrls:
    def test_returns_pending_urls(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
            {"url": FAKE_URL_2, "status": ""},
        ]
        result = get_new_urls()
        assert len(result) == 2
        assert result[0] == {"row_index": 0, "url": FAKE_URL_1}
        assert result[1] == {"row_index": 1, "url": FAKE_URL_2}

    def test_skips_done_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": "done"},
        ]
        assert get_new_urls() == []
        mock_sheets.update_input_status.assert_not_called()

    def test_skips_processing_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": "processing"},
        ]
        assert get_new_urls() == []

    def test_marks_existing_product_as_done(self, mock_sheets):
        with _db.SessionLocal() as session:
            session.add(
                SourceProduct(
                    url=FAKE_URL_1, slug="test-trigger-1", title="Test", specs={}
                )
            )
            session.commit()

        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
        ]
        result = get_new_urls()
        assert result == []
        mock_sheets.update_input_status.assert_called_once_with(0, "done")

    def test_skips_empty_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": "", "status": ""},
        ]
        assert get_new_urls() == []
