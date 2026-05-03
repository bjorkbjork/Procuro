"""
Tests for the Stage 1 integration: poll input sheet → extract specs → update status.
All external calls (Sheets API, Browserbase) are monkeypatched.
"""

from unittest.mock import MagicMock, call

import pytest

from app.base.config import PROJECT_ROOT
from app.pipeline.agents.main import process_input_sheet
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct

FIXTURES_DIR = PROJECT_ROOT / "html_test_fixtures"
FIXTURE_PATH = (
    FIXTURES_DIR
    / "Buy Kogan 75_ QLED 4K Smart AI Google TV - Q97T Online _ Kogan.com.html"
)
FAKE_URL_1 = "https://www.kogan.com/au/buy/test-main-1/"
FAKE_URL_2 = "https://www.kogan.com/au/buy/test-main-2/"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal() as session:
        session.query(SourceProduct).filter(
            SourceProduct.url.in_([FAKE_URL_1, FAKE_URL_2])
        ).delete()
        session.commit()


@pytest.fixture
def mock_sheets(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("app.pipeline.agents.main.SheetsService", lambda: mock)
    return mock


@pytest.fixture(autouse=True)
def mock_fetch(monkeypatch):
    html = FIXTURE_PATH.read_text()
    monkeypatch.setattr(
        "app.pipeline.stages.s1_spec_extraction.fetch_page_html",
        lambda url, proxy_country=None: html,
    )


class TestProcessInputSheet:
    def test_extracts_new_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
        ]
        process_input_sheet()

        mock_sheets.update_input_status.assert_any_call(0, "processing")
        mock_sheets.update_input_status.assert_any_call(0, "done")

        with SessionLocal() as session:
            product = session.query(SourceProduct).filter_by(url=FAKE_URL_1).first()
            assert product is not None
            assert product.specs is not None

    def test_skips_done_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": "done"},
        ]
        process_input_sheet()
        mock_sheets.update_input_status.assert_not_called()

    def test_skips_processing_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": "processing"},
        ]
        process_input_sheet()
        mock_sheets.update_input_status.assert_not_called()

    def test_marks_existing_product_as_done(self, mock_sheets):
        with SessionLocal() as session:
            session.add(
                SourceProduct(
                    url=FAKE_URL_1, slug="test-main-1", title="Test", specs={}
                )
            )
            session.commit()

        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
        ]
        process_input_sheet()
        mock_sheets.update_input_status.assert_called_once_with(0, "done")

    def test_marks_error_on_failure(self, mock_sheets, monkeypatch):
        monkeypatch.setattr(
            "app.pipeline.stages.s1_spec_extraction.fetch_page_html",
            lambda url, proxy_country=None: (_ for _ in ()).throw(
                RuntimeError("captcha")
            ),
        )
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
        ]
        process_input_sheet()
        mock_sheets.update_input_status.assert_any_call(0, "processing")
        mock_sheets.update_input_status.assert_any_call(0, "error")

    def test_processes_multiple_urls(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": FAKE_URL_1, "status": ""},
            {"url": FAKE_URL_2, "status": ""},
        ]
        process_input_sheet()
        assert mock_sheets.update_input_status.call_count == 4
        mock_sheets.update_input_status.assert_any_call(0, "done")
        mock_sheets.update_input_status.assert_any_call(1, "done")

    def test_skips_empty_url(self, mock_sheets):
        mock_sheets.read_input_rows.return_value = [
            {"url": "", "status": ""},
        ]
        process_input_sheet()
        mock_sheets.update_input_status.assert_not_called()
