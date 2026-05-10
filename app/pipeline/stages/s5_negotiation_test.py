"""Tests for Stage 5 negotiation orchestrator. Mocks Gmail and LLM agents —
tests state transitions, quote recording, spec check gating, reply delays,
and the silence/close paths."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.pipeline.agents.match_agent import MatchResult
from app.pipeline.agents.negotiation_agent import (
    ExtractedQuote,
    NegotiationAction,
    NegotiationResult,
)
from app.db import database as _db
from app.db.models.message import Message
from app.db.models.quote import Quote
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.stages.s5_negotiation import (
    _get_ready_threads,
    _process_thread,
    _record_quote,
    _run_spec_check,
    process_negotiations,
)


@pytest.fixture
def thread_awaiting_reply():
    """Thread in AWAITING_REPLY with one outbound and one inbound message."""
    with _db.SessionLocal() as session:
        source = SourceProduct(
            url="https://www.kogan.com/au/buy/test-negotiate/",
            slug="test-negotiate",
            title='Test 75" QLED TV',
            specs={"Display": {"Size": '75"', "Type": "QLED", "Resolution": "4K"}},
        )
        session.add(source)
        session.flush()

        supplier = Supplier(
            name="Negotiate Test Supplier",
            platform="alibaba",
            profile_url="https://negotiate-test.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/negotiate_111.html",
            title="75 inch QLED Smart TV 4K",
            specs={"Size": "75 inch", "Type": "QLED", "Resolution": "3840x2160"},
        )
        session.add(sup_product)
        session.flush()

        thread = SupplierThread(
            source_product_id=source.id,
            supplier_product_id=sup_product.id,
            supplier_id=supplier.id,
            state="AWAITING_REPLY",
            gmail_thread_id="gmail_t_negotiate",
            negotiation_rounds=0,
        )
        session.add(thread)
        session.flush()

        session.add(
            Message(
                thread_id=thread.id,
                direction="outbound",
                subject="Initial outreach",
                body="We are looking to source a 75 inch QLED TV...",
            )
        )
        session.add(
            Message(
                thread_id=thread.id,
                direction="inbound",
                gmail_message_id="gmsg_supplier_reply_1",
                subject="Re: Product Inquiry",
                body="Thank you for your inquiry. Our best price is $200 FOB, MOQ 300.",
            )
        )
        session.commit()
        session.refresh(thread)
        thread_id = thread.id
        source_id = source.id
        supplier_id = supplier.id
        sup_product_id = sup_product.id

    yield thread_id

    with _db.SessionLocal() as session:
        session.query(Quote).filter_by(thread_id=thread_id).delete()
        session.query(Message).filter_by(thread_id=thread_id).delete()
        session.query(SupplierThread).filter_by(id=thread_id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product_id).delete()
        session.query(Supplier).filter_by(id=supplier_id).delete()
        session.query(SourceProduct).filter_by(id=source_id).delete()
        session.commit()


@pytest.fixture
def thread_negotiating():
    """Thread in NEGOTIATING with multiple rounds of messages."""
    with _db.SessionLocal() as session:
        source = SourceProduct(
            url="https://www.kogan.com/au/buy/test-negotiate-round2/",
            slug="test-negotiate-round2",
            title="Test Air Fryer",
            specs={"Capacity": {"Size": "45L"}},
        )
        session.add(source)
        session.flush()

        supplier = Supplier(
            name="Round 2 Supplier",
            platform="alibaba",
            profile_url="https://round2-supplier.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/round2_222.html",
            title="45L Digital Air Fryer Oven",
            specs={"Capacity": "45L"},
        )
        session.add(sup_product)
        session.flush()

        thread = SupplierThread(
            source_product_id=source.id,
            supplier_product_id=sup_product.id,
            supplier_id=supplier.id,
            state="NEGOTIATING",
            gmail_thread_id="gmail_t_round2",
            negotiation_rounds=1,
        )
        session.add(thread)
        session.flush()

        session.add(
            Message(
                thread_id=thread.id,
                direction="outbound",
                subject="Initial outreach",
                body="Looking for 45L air fryer...",
            )
        )
        session.add(
            Message(
                thread_id=thread.id,
                direction="inbound",
                gmail_message_id="gmsg_r2_1",
                subject="Re: Inquiry",
                body="Price $50 FOB, MOQ 1000",
            )
        )
        session.add(
            Message(
                thread_id=thread.id,
                direction="outbound",
                subject="Re: Inquiry",
                body="That's too high, we need better pricing.",
            )
        )
        session.add(
            Message(
                thread_id=thread.id,
                direction="inbound",
                gmail_message_id="gmsg_r2_2",
                subject="Re: Inquiry",
                body="Best we can do is $42 FOB",
            )
        )
        session.commit()
        session.refresh(thread)
        thread_id = thread.id
        source_id = source.id
        supplier_id = supplier.id
        sup_product_id = sup_product.id

    yield thread_id

    with _db.SessionLocal() as session:
        session.query(Quote).filter_by(thread_id=thread_id).delete()
        session.query(Message).filter_by(thread_id=thread_id).delete()
        session.query(SupplierThread).filter_by(id=thread_id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product_id).delete()
        session.query(Supplier).filter_by(id=supplier_id).delete()
        session.query(SourceProduct).filter_by(id=source_id).delete()
        session.commit()


def _mock_gmail():
    mock = MagicMock()
    mock.reply_to_thread.return_value = {"id": "gmsg_reply_out"}
    mock.get_thread.return_value = {
        "messages": [
            {
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sales@supplier-factory.cn"},
                        {"name": "Subject", "value": "Re: Product Inquiry"},
                    ],
                },
            }
        ],
    }
    return mock


# ---------------------------------------------------------------------------
# _get_ready_threads
# ---------------------------------------------------------------------------


class TestGetReadyThreads:
    def test_picks_up_awaiting_reply(self, thread_awaiting_reply):
        ids = _get_ready_threads()
        assert thread_awaiting_reply in ids

    def test_picks_up_negotiating(self, thread_negotiating):
        ids = _get_ready_threads()
        assert thread_negotiating in ids

    def test_skips_thread_with_future_respond_after(self, thread_awaiting_reply):
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            thread.respond_after = datetime.now(timezone.utc) + timedelta(hours=24)
            session.commit()

        ids = _get_ready_threads()
        assert thread_awaiting_reply not in ids

    def test_includes_thread_with_past_respond_after(self, thread_awaiting_reply):
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            thread.respond_after = datetime.now(timezone.utc) - timedelta(hours=1)
            session.commit()

        ids = _get_ready_threads()
        assert thread_awaiting_reply in ids


# ---------------------------------------------------------------------------
# _run_spec_check
# ---------------------------------------------------------------------------


class TestRunSpecCheck:
    def test_pass_sets_state(self, thread_awaiting_reply):
        match_result = MatchResult(
            is_match=True,
            confidence=0.9,
            reasoning="Specs match",
            key_differences=[],
        )
        with patch(
            "app.pipeline.stages.s5_negotiation.compare_products",
            return_value=match_result,
        ):
            passed = _run_spec_check(thread_awaiting_reply)

        assert passed is True
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            assert thread.state == "SPEC_CHECK_PASS"

    def test_fail_sets_state(self, thread_awaiting_reply):
        match_result = MatchResult(
            is_match=False,
            confidence=0.3,
            reasoning="Wrong panel type",
            key_differences=["OLED vs QLED"],
        )
        with patch(
            "app.pipeline.stages.s5_negotiation.compare_products",
            return_value=match_result,
        ):
            passed = _run_spec_check(thread_awaiting_reply)

        assert passed is False
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            assert thread.state == "SPEC_CHECK_FAIL"


# ---------------------------------------------------------------------------
# _record_quote
# ---------------------------------------------------------------------------


class TestRecordQuote:
    def test_records_quote(self, thread_awaiting_reply):
        _record_quote(thread_awaiting_reply, 200.0, 300, "30-45 days")
        with _db.SessionLocal() as session:
            quotes = (
                session.query(Quote).filter_by(thread_id=thread_awaiting_reply).all()
            )
            assert len(quotes) == 1
            assert float(quotes[0].price_usd) == 200.0
            assert quotes[0].moq == 300
            assert quotes[0].round_number == 1

    def test_skips_when_no_price(self, thread_awaiting_reply):
        _record_quote(thread_awaiting_reply, None, 300, "30 days")
        with _db.SessionLocal() as session:
            quotes = (
                session.query(Quote).filter_by(thread_id=thread_awaiting_reply).all()
            )
            assert len(quotes) == 0


# ---------------------------------------------------------------------------
# _process_thread — spec check path
# ---------------------------------------------------------------------------


class TestProcessThreadSpecCheck:
    def test_spec_fail_closes_thread(self, thread_awaiting_reply):
        match_result = MatchResult(
            is_match=False,
            confidence=0.2,
            reasoning="Wrong size",
            key_differences=["65 inch vs 75 inch"],
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.compare_products",
            return_value=match_result,
        ):
            status = _process_thread(thread_awaiting_reply, reply_fn, "email")

        assert status == "spec_check_fail"
        reply_fn.assert_called_once()
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            assert thread.state == "CLOSED"

    def test_spec_pass_proceeds_to_negotiation(self, thread_awaiting_reply):
        match_result = MatchResult(
            is_match=True,
            confidence=0.95,
            reasoning="Good match",
            key_differences=[],
        )
        neg_result = NegotiationResult(
            action=NegotiationAction.REPLY,
            reply_text="That price is too high.",
            extracted_quote=ExtractedQuote(price_usd=200.0, moq=300),
            reasoning="First round pushback",
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with (
            patch(
                "app.pipeline.stages.s5_negotiation.compare_products",
                return_value=match_result,
            ),
            patch(
                "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
            ),
        ):
            status = _process_thread(thread_awaiting_reply, reply_fn, "email")

        assert status == "replied"
        reply_fn.assert_called_once()
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            assert thread.state == "NEGOTIATING"
            assert thread.negotiation_rounds == 1
            assert thread.respond_after is not None


# ---------------------------------------------------------------------------
# _process_thread — negotiation paths
# ---------------------------------------------------------------------------


class TestProcessThreadNegotiation:
    def test_reply_action(self, thread_negotiating):
        neg_result = NegotiationResult(
            action=NegotiationAction.REPLY,
            reply_text="We need you to come down further.",
            extracted_quote=ExtractedQuote(price_usd=42.0, moq=1000),
            reasoning="Still room to negotiate",
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
        ):
            status = _process_thread(thread_negotiating, reply_fn, "email")

        assert status == "replied"
        reply_fn.assert_called_once()
        reply_body = reply_fn.call_args.args[0]
        assert "come down further" in reply_body

        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_negotiating)
            assert thread.state == "NEGOTIATING"
            assert thread.negotiation_rounds == 2
            assert thread.respond_after > datetime.now(timezone.utc)

            quotes = session.query(Quote).filter_by(thread_id=thread_negotiating).all()
            assert len(quotes) == 1
            assert float(quotes[0].price_usd) == 42.0

    def test_silence_action(self, thread_negotiating):
        neg_result = NegotiationResult(
            action=NegotiationAction.SILENCE,
            extracted_quote=ExtractedQuote(price_usd=42.0),
            reasoning="Price not competitive enough",
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
        ):
            status = _process_thread(thread_negotiating, reply_fn, "email")

        assert status == "silence"
        reply_fn.assert_not_called()

        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_negotiating)
            assert thread.respond_after > datetime.now(timezone.utc)

    def test_close_with_price_sets_final_price_logged(self, thread_negotiating):
        neg_result = NegotiationResult(
            action=NegotiationAction.CLOSE,
            reply_text="Thank you, we'll proceed with this pricing.",
            extracted_quote=ExtractedQuote(price_usd=38.0, moq=500),
            reasoning="Good final price after 3 rounds",
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
        ):
            status = _process_thread(thread_negotiating, reply_fn, "email")

        assert status == "closed"
        reply_fn.assert_called_once()

        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_negotiating)
            assert thread.state == "FINAL_PRICE_LOGGED"

    def test_close_without_price_sets_closed(self, thread_negotiating):
        neg_result = NegotiationResult(
            action=NegotiationAction.CLOSE,
            reply_text="We'll pass, thank you.",
            extracted_quote=ExtractedQuote(),
            reasoning="Supplier won't budge",
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
        ):
            status = _process_thread(thread_negotiating, reply_fn, "email")

        assert status == "closed"
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_negotiating)
            assert thread.state == "CLOSED"

    def test_records_outbound_message(self, thread_negotiating):
        neg_result = NegotiationResult(
            action=NegotiationAction.REPLY,
            reply_text="Need better pricing.",
            extracted_quote=ExtractedQuote(),
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with patch(
            "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
        ):
            _process_thread(thread_negotiating, reply_fn, "email")

        with _db.SessionLocal() as session:
            outbound = (
                session.query(Message)
                .filter_by(
                    thread_id=thread_negotiating,
                    direction="outbound",
                    gmail_message_id="gmsg_reply_out",
                )
                .first()
            )
            assert outbound is not None
            assert outbound.body == "Need better pricing."


# ---------------------------------------------------------------------------
# _process_thread — edge cases
# ---------------------------------------------------------------------------


class TestProcessThreadEdgeCases:
    def test_skips_spec_check_on_later_rounds(self, thread_negotiating):
        """Threads already in NEGOTIATING skip spec check."""
        neg_result = NegotiationResult(
            action=NegotiationAction.REPLY,
            reply_text="Lower please.",
            extracted_quote=ExtractedQuote(price_usd=42.0),
        )
        reply_fn = MagicMock(return_value="gmsg_reply_out")

        with (
            patch(
                "app.pipeline.stages.s5_negotiation.compare_products"
            ) as mock_compare,
            patch(
                "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
            ),
        ):
            _process_thread(thread_negotiating, reply_fn, "email")

        mock_compare.assert_not_called()


# ---------------------------------------------------------------------------
# process_negotiations (top-level)
# ---------------------------------------------------------------------------


class TestProcessNegotiations:
    def test_processes_ready_threads(self, thread_awaiting_reply):
        match_result = MatchResult(
            is_match=True,
            confidence=0.9,
            reasoning="Match",
            key_differences=[],
        )
        neg_result = NegotiationResult(
            action=NegotiationAction.REPLY,
            reply_text="Too high.",
            extracted_quote=ExtractedQuote(price_usd=200.0),
        )
        gmail = _mock_gmail()

        with (
            patch(
                "app.pipeline.stages.s5_negotiation.GmailService", return_value=gmail
            ),
            patch(
                "app.pipeline.stages.s5_negotiation.compare_products",
                return_value=match_result,
            ),
            patch(
                "app.pipeline.stages.s5_negotiation.negotiate", return_value=neg_result
            ),
        ):
            counts = process_negotiations()

        assert counts.get("replied", 0) >= 1

    def test_empty_when_no_threads(self):
        with patch(
            "app.pipeline.stages.s5_negotiation._get_ready_threads", return_value=[]
        ):
            counts = process_negotiations()

        assert counts == {}

    def test_skips_thread_without_gmail_thread_id(self, thread_awaiting_reply):
        """Email-channel threads without gmail_thread_id are skipped."""
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, thread_awaiting_reply)
            thread.gmail_thread_id = None
            session.commit()

        with patch(
            "app.pipeline.stages.s5_negotiation.GmailService",
            return_value=_mock_gmail(),
        ):
            counts = process_negotiations()

        assert counts.get("no_channel", 0) >= 1

    def test_error_in_one_thread_doesnt_stop_others(
        self, thread_awaiting_reply, thread_negotiating
    ):
        gmail = _mock_gmail()

        call_count = 0

        def failing_then_ok(thread_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated failure")
            from app.pipeline.stages.s5_negotiation import NegotiationDecision

            return NegotiationDecision(
                thread_id=thread_id, status="replied", reply_text="counter"
            )

        with (
            patch(
                "app.pipeline.stages.s5_negotiation.GmailService", return_value=gmail
            ),
            patch(
                "app.pipeline.stages.s5_negotiation._negotiate_thread",
                side_effect=failing_then_ok,
            ),
        ):
            counts = process_negotiations()

        assert counts.get("error", 0) == 1
        assert counts.get("replied", 0) == 1


# ---------------------------------------------------------------------------
# _fetch_pdf_attachments
# ---------------------------------------------------------------------------


class TestFetchPdfAttachments:
    def test_returns_none_when_no_attachments(self):
        from app.pipeline.stages.s5_negotiation import _fetch_pdf_attachments

        msg = Message(
            thread_id=1,
            direction="inbound",
            body="Hello",
            attachments=None,
        )
        assert _fetch_pdf_attachments(msg) is None

    def test_fetches_pdf_bytes(self):
        from pydantic_ai.messages import BinaryContent

        from app.pipeline.stages.s5_negotiation import _fetch_pdf_attachments

        msg = Message(
            thread_id=1,
            direction="inbound",
            body="See attached quote",
            attachments=[
                {
                    "filename": "quote.pdf",
                    "mime_type": "application/pdf",
                    "size": 1000,
                    "attachment_id": "ATT1",
                    "gmail_message_id": "msg_abc",
                }
            ],
        )
        mock_gmail = MagicMock()
        mock_gmail.get_attachment.return_value = b"%PDF-fake-content"

        with patch(
            "app.pipeline.stages.s5_negotiation.GmailService",
            return_value=mock_gmail,
        ):
            result = _fetch_pdf_attachments(msg)

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], BinaryContent)
        assert result[0].data == b"%PDF-fake-content"
        assert result[0].media_type == "application/pdf"
        mock_gmail.get_attachment.assert_called_once_with("msg_abc", "ATT1")

    def test_skips_failed_download(self):
        from app.pipeline.stages.s5_negotiation import _fetch_pdf_attachments

        msg = Message(
            thread_id=1,
            direction="inbound",
            body="See attached",
            attachments=[
                {
                    "filename": "broken.pdf",
                    "mime_type": "application/pdf",
                    "size": 500,
                    "attachment_id": "ATT_BROKEN",
                    "gmail_message_id": "msg_xyz",
                }
            ],
        )
        mock_gmail = MagicMock()
        mock_gmail.get_attachment.side_effect = RuntimeError("API error")

        with patch(
            "app.pipeline.stages.s5_negotiation.GmailService",
            return_value=mock_gmail,
        ):
            result = _fetch_pdf_attachments(msg)

        assert result is None
