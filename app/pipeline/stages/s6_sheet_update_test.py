"""Tests for Stage 6 sheet update. Mocks SheetsService — tests row building
logic, ordering, and handling of threads with/without quotes."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.db import database as _db
from app.db.models.message import Message
from app.db.models.quote import Quote
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.stages.s6_sheet_update import _build_row, update_sheet

TEST_URL = "https://www.kogan.com/au/buy/test-sheet-update/"


@pytest.fixture
def thread_with_quote():
    """Create a full thread with an outbound message and a quote."""
    with _db.SessionLocal() as session:
        source = SourceProduct(
            url=TEST_URL,
            slug="test-sheet-update",
            title="Test Sheet Product",
            specs={"Display": {"Size": '75"'}},
        )
        session.add(source)
        session.flush()

        supplier = Supplier(
            name="Sheet Test Supplier",
            platform="alibaba",
            profile_url="https://sheet-test.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/sheet_test_456.html",
            title="Sheet Test TV",
        )
        session.add(sup_product)
        session.flush()

        thread = SupplierThread(
            source_product_id=source.id,
            supplier_product_id=sup_product.id,
            supplier_id=supplier.id,
            state="NEGOTIATING",
            gmail_thread_id="gmail_thread_abc",
        )
        session.add(thread)
        session.flush()

        outbound = Message(
            thread_id=thread.id,
            direction="outbound",
            subject="Initial outreach",
            body="Hi, we are looking for...",
            sent_at=datetime.now(timezone.utc),
        )
        session.add(outbound)

        quote = Quote(
            thread_id=thread.id,
            round_number=1,
            price_usd=150.00,
            moq=500,
            lead_time="30-45 days",
        )
        session.add(quote)
        session.commit()
        session.refresh(thread)
        yield thread

        session.query(Quote).filter_by(thread_id=thread.id).delete()
        session.query(Message).filter_by(thread_id=thread.id).delete()
        session.query(SupplierThread).filter_by(id=thread.id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product.id).delete()
        session.query(Supplier).filter_by(id=supplier.id).delete()
        session.query(SourceProduct).filter_by(id=source.id).delete()
        session.commit()


@pytest.fixture
def thread_without_quote():
    """Create a thread in OUTREACH_SENT with no quotes yet."""
    with _db.SessionLocal() as session:
        source = SourceProduct(
            url="https://www.kogan.com/au/buy/test-no-quote/",
            slug="test-no-quote",
            title="No Quote Product",
            specs={},
        )
        session.add(source)
        session.flush()

        supplier = Supplier(
            name="No Quote Supplier",
            platform="alibaba",
            profile_url="https://no-quote.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/no_quote_789.html",
            title="No Quote TV",
        )
        session.add(sup_product)
        session.flush()

        thread = SupplierThread(
            source_product_id=source.id,
            supplier_product_id=sup_product.id,
            supplier_id=supplier.id,
            state="OUTREACH_SENT",
        )
        session.add(thread)
        session.flush()

        outbound = Message(
            thread_id=thread.id,
            direction="outbound",
            subject="Initial outreach",
            body="Hi...",
        )
        session.add(outbound)
        session.commit()
        session.refresh(thread)
        yield thread

        session.query(Message).filter_by(thread_id=thread.id).delete()
        session.query(SupplierThread).filter_by(id=thread.id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product.id).delete()
        session.query(Supplier).filter_by(id=supplier.id).delete()
        session.query(SourceProduct).filter_by(id=source.id).delete()
        session.commit()


class TestBuildRow:
    def test_with_quote(self, thread_with_quote):
        row = _build_row(thread_with_quote)
        assert row["source_product_title"] == "Test Sheet Product"
        assert row["source_link"] == TEST_URL
        assert row["source_slug"] == "test-sheet-update"
        assert row["supplier_name"] == "Sheet Test Supplier"
        assert row["best_price_usd_fob"] == "150.00"
        assert row["moq"] == "500"
        assert row["lead_time"] == "30-45 days"
        assert "gmail_thread_abc" in row["email_chain"]
        assert row["initial_outreach_date"] != ""

    def test_without_quote(self, thread_without_quote):
        row = _build_row(thread_without_quote)
        assert row["source_slug"] == "test-no-quote"
        assert row["supplier_name"] == "No Quote Supplier"
        assert row["best_price_usd_fob"] == "Awaiting Quotes"
        assert row["moq"] == ""
        assert row["lead_time"] == ""
        assert row["email_chain"] == ""

    def test_no_gmail_thread(self, thread_without_quote):
        row = _build_row(thread_without_quote)
        assert row["email_chain"] == ""


class TestUpdateSheet:
    def test_upserts_non_new_threads(self, thread_with_quote):
        mock_sheets = MagicMock()

        with patch(
            "app.pipeline.stages.s6_sheet_update.SheetsService",
            return_value=mock_sheets,
        ):
            count = update_sheet()

        assert count >= 1
        mock_sheets.upsert_output_row.assert_called()
        rows_written = [
            call.args[0] for call in mock_sheets.upsert_output_row.call_args_list
        ]
        slugs = [r["source_slug"] for r in rows_written]
        assert "test-sheet-update" in slugs

    def test_skips_new_threads(self):
        """NEW threads should not appear in the sheet."""
        with _db.SessionLocal() as session:
            source = SourceProduct(
                url="https://www.kogan.com/au/buy/test-new-skip/",
                slug="test-new-skip",
                title="Skip Me",
                specs={},
            )
            session.add(source)
            session.flush()
            supplier = Supplier(
                name="Skip Supplier",
                platform="alibaba",
                profile_url="https://skip.alibaba.com",
            )
            session.add(supplier)
            session.flush()
            sup_product = SupplierProduct(
                source_product_id=source.id,
                supplier_id=supplier.id,
                platform="alibaba",
                product_url="https://www.alibaba.com/product-detail/skip_000.html",
                title="Skip TV",
            )
            session.add(sup_product)
            session.flush()
            thread = SupplierThread(
                source_product_id=source.id,
                supplier_product_id=sup_product.id,
                supplier_id=supplier.id,
                state="NEW",
            )
            session.add(thread)
            session.commit()
            thread_id = thread.id
            sp_id = sup_product.id
            s_id = supplier.id
            src_id = source.id

        mock_sheets = MagicMock()
        with patch(
            "app.pipeline.stages.s6_sheet_update.SheetsService",
            return_value=mock_sheets,
        ):
            update_sheet()

        rows_written = [
            call.args[0] for call in mock_sheets.upsert_output_row.call_args_list
        ]
        slugs = [r["source_slug"] for r in rows_written]
        assert "test-new-skip" not in slugs

        with _db.SessionLocal() as session:
            session.query(SupplierThread).filter_by(id=thread_id).delete()
            session.query(SupplierProduct).filter_by(id=sp_id).delete()
            session.query(Supplier).filter_by(id=s_id).delete()
            session.query(SourceProduct).filter_by(id=src_id).delete()
            session.commit()

    def test_handles_upsert_error_gracefully(self, thread_with_quote):
        mock_sheets = MagicMock()
        mock_sheets.upsert_output_row.side_effect = RuntimeError("Sheets API error")

        with patch(
            "app.pipeline.stages.s6_sheet_update.SheetsService",
            return_value=mock_sheets,
        ):
            count = update_sheet()

        assert count == 0

    def test_empty_db(self):
        mock_sheets = MagicMock()
        with patch(
            "app.pipeline.stages.s6_sheet_update.SheetsService",
            return_value=mock_sheets,
        ):
            count = update_sheet()

        # May be 0 or may pick up other test fixtures, but shouldn't crash
        mock_sheets.upsert_output_row.assert_not_called() if count == 0 else None
