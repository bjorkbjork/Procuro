"""
Integration tests for the Google Sheets service.
Runs against the real spreadsheet — no mocks.
"""

import pytest

from app.services.sheets import SheetsService

pytestmark = pytest.mark.integration

INPUT_TAB = "Input"
OUTPUT_TAB = "Output"


@pytest.fixture(scope="module")
def sheets():
    return SheetsService()


class TestAuthentication:
    def test_service_builds_successfully(self, sheets: SheetsService):
        assert sheets.service is not None

    def test_can_read_spreadsheet_metadata(self, sheets: SheetsService):
        meta = sheets.get_spreadsheet_metadata()
        assert meta is not None
        tab_titles = [s["properties"]["title"] for s in meta["sheets"]]
        assert INPUT_TAB in tab_titles
        assert OUTPUT_TAB in tab_titles


class TestInputTab:
    def test_read_input_rows(self, sheets: SheetsService):
        rows = sheets.read_input_rows()
        assert isinstance(rows, list)

    def test_read_input_rows_have_url_field(self, sheets: SheetsService):
        rows = sheets.read_input_rows()
        if len(rows) > 0:
            assert "url" in rows[0]

    def test_update_input_status(self, sheets: SheetsService):
        rows = sheets.read_input_rows()
        if len(rows) == 0:
            pytest.skip("No rows in input tab to test status update")
        first_row_index = 0
        original_status = rows[first_row_index].get("status", "")
        sheets.update_input_status(first_row_index, "processing")
        updated_rows = sheets.read_input_rows()
        assert updated_rows[first_row_index]["status"] == "processing"
        # restore original
        sheets.update_input_status(first_row_index, original_status)


class TestOutputTab:
    def test_read_output_rows(self, sheets: SheetsService):
        rows = sheets.read_output_rows()
        assert isinstance(rows, list)

    def test_upsert_output_row_inserts_new(self, sheets: SheetsService):
        rows_before = sheets.read_output_rows()
        sheets.upsert_output_row({
            "source_product_title": "TEST PRODUCT",
            "source_link": "https://www.kogan.com/au/buy/test-product-slug/",
            "supplier_name": "Test Supplier Co",
            "best_price_usd_fob": "99.99",
            "moq": "500",
            "lead_time": "30 days",
            "email_chain": "",
            "last_updated_date": "2026-04-30",
            "initial_outreach_date": "2026-04-30",
        })
        rows_after = sheets.read_output_rows()
        assert len(rows_after) == len(rows_before) + 1
        new_row = rows_after[-1]
        assert new_row["source_product_title"] == "TEST PRODUCT"
        assert new_row["supplier_name"] == "Test Supplier Co"
        assert new_row["source_slug"] == "test-product-slug/"

    def test_upsert_output_row_updates_existing(self, sheets: SheetsService):
        sheets.upsert_output_row({
            "source_product_title": "TEST PRODUCT",
            "source_link": "https://www.kogan.com/au/buy/test-product-slug/",
            "supplier_name": "Test Supplier Co",
            "best_price_usd_fob": "79.99",
            "moq": "1000",
            "lead_time": "25 days",
            "email_chain": "",
            "last_updated_date": "2026-04-30",
            "initial_outreach_date": "2026-04-30",
        })
        rows = sheets.read_output_rows()
        matches = [r for r in rows if r["supplier_name"] == "Test Supplier Co"
                   and r["source_slug"] == "test-product-slug/"]
        assert len(matches) == 1
        assert matches[0]["best_price_usd_fob"] == "79.99"
        assert matches[0]["moq"] == "1000"

    def test_upsert_output_row_cleanup(self, sheets: SheetsService):
        """Remove the test row added by previous tests."""
        sheets.delete_output_row("test-product-slug/", "Test Supplier Co")
        rows = sheets.read_output_rows()
        matches = [r for r in rows if r["supplier_name"] == "Test Supplier Co"
                   and r["source_slug"] == "test-product-slug/"]
        assert len(matches) == 0
