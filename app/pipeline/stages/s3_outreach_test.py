"""Tests for Stage 3 outreach. Mocks browser and platform — tests the
orchestration logic (thread selection, message building, state transitions)."""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.stages.s3_outreach import (
    _build_message,
    _format_spec_block,
    send_outreach,
)

TEST_SPECS = {
    "Display": {"Screen Size": '75"', "Screen Type": "QLED"},
    "Audio": {"Speaker Output": "20W"},
}
TEST_URL = "https://www.kogan.com/au/buy/test-outreach/"


@pytest.fixture
def source_product():
    with SessionLocal() as session:
        sp = SourceProduct(
            url=TEST_URL,
            slug="test-outreach",
            title="Test TV",
            specs=TEST_SPECS,
        )
        session.add(sp)
        session.commit()
        session.refresh(sp)
        yield sp
        session.query(SourceProduct).filter_by(id=sp.id).delete()
        session.commit()


@pytest.fixture
def supplier_and_thread(source_product):
    with SessionLocal() as session:
        supplier = Supplier(
            name="Test Supplier",
            platform="alibaba",
            profile_url="https://test-supplier.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source_product.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/test_123.html",
            title="Test Supplier TV",
            specs=TEST_SPECS,
        )
        session.add(sup_product)
        session.flush()

        thread = SupplierThread(
            source_product_id=source_product.id,
            supplier_product_id=sup_product.id,
            supplier_id=supplier.id,
            state="NEW",
        )
        session.add(thread)
        session.commit()
        session.refresh(thread)
        yield thread

        session.query(Message).filter_by(thread_id=thread.id).delete()
        session.query(SupplierThread).filter_by(id=thread.id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product.id).delete()
        session.query(Supplier).filter_by(id=supplier.id).delete()
        session.commit()


class TestFormatSpecBlock:
    def test_formats_groups(self):
        result = _format_spec_block(TEST_SPECS)
        assert "Display:" in result
        assert '  Screen Size: 75"' in result
        assert "  Screen Type: QLED" in result
        assert "Audio:" in result

    def test_empty_specs(self):
        assert _format_spec_block({}) == ""


class TestBuildMessage:
    def test_includes_specs_and_url(self, source_product):
        msg = _build_message(source_product)
        assert "QLED" in msg
        assert TEST_URL in msg
        assert "the agent" in msg

    def test_includes_email(self, source_product):
        msg = _build_message(source_product)
        assert "email" in msg.lower()
        assert "@" in msg


class TestSendOutreach:
    def _mock_threads(self, source_product, product_url, thread_id):
        """Return a _get_threads_by_platform result scoped to one thread."""
        return {
            "alibaba": [
                {
                    "thread_id": thread_id,
                    "product_url": product_url,
                    "source_product": source_product,
                }
            ]
        }

    def test_sends_inquiry_and_updates_state(self, supplier_and_thread, source_product):
        thread = supplier_and_thread
        mock_platform = MagicMock()
        mock_platform.platform.value = "alibaba"
        mock_platform.send_inquiry.return_value = True

        mock_browser = MagicMock()
        mock_browser.__enter__ = MagicMock(return_value=mock_browser)
        mock_browser.__exit__ = MagicMock(return_value=False)

        with SessionLocal() as session:
            sp = session.get(SupplierProduct, thread.supplier_product_id)
            product_url = sp.product_url

        grouped = self._mock_threads(source_product, product_url, thread.id)

        with (
            patch(
                "app.pipeline.stages.s3_outreach.get_platforms",
                return_value=[mock_platform],
            ),
            patch(
                "app.pipeline.stages.s3_outreach.BrowserSession",
                return_value=mock_browser,
            ),
            patch(
                "app.pipeline.stages.s3_outreach._get_threads_by_platform",
                return_value=grouped,
            ),
        ):
            count = send_outreach()

        assert count == 1
        mock_platform.login.assert_called_once()
        mock_platform.send_inquiry.assert_called_once()

        with SessionLocal() as session:
            updated = session.get(SupplierThread, thread.id)
            assert updated.state == "OUTREACH_SENT"
            msgs = session.query(Message).filter_by(thread_id=thread.id).all()
            assert len(msgs) == 1
            assert msgs[0].direction == "outbound"
            assert "QLED" in msgs[0].body

    def test_skips_on_inquiry_failure(self, supplier_and_thread, source_product):
        thread = supplier_and_thread
        mock_platform = MagicMock()
        mock_platform.platform.value = "alibaba"
        mock_platform.send_inquiry.side_effect = RuntimeError("captcha")

        mock_browser = MagicMock()
        mock_browser.__enter__ = MagicMock(return_value=mock_browser)
        mock_browser.__exit__ = MagicMock(return_value=False)

        with SessionLocal() as session:
            sp = session.get(SupplierProduct, thread.supplier_product_id)
            product_url = sp.product_url

        grouped = self._mock_threads(source_product, product_url, thread.id)

        with (
            patch(
                "app.pipeline.stages.s3_outreach.get_platforms",
                return_value=[mock_platform],
            ),
            patch(
                "app.pipeline.stages.s3_outreach.BrowserSession",
                return_value=mock_browser,
            ),
            patch(
                "app.pipeline.stages.s3_outreach._get_threads_by_platform",
                return_value=grouped,
            ),
        ):
            count = send_outreach()

        assert count == 0
        with SessionLocal() as session:
            updated = session.get(SupplierThread, thread.id)
            assert updated.state == "NEW"

    def test_no_new_threads_is_noop(self):
        mock_platform = MagicMock()
        mock_platform.platform.value = "alibaba"

        with (
            patch(
                "app.pipeline.stages.s3_outreach.get_platforms",
                return_value=[mock_platform],
            ),
            patch(
                "app.pipeline.stages.s3_outreach._get_threads_by_platform",
                return_value={},
            ),
        ):
            count = send_outreach()

        assert count == 0
