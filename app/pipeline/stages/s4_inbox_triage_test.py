"""Tests for Stage 4 inbox triage. Mocks Gmail and LLM — tests the
three-tier filter logic, ignore list management, and state transitions."""

from unittest.mock import MagicMock, patch

import pytest

from app.db import database as _db
from app.db.models.keyvalue import KeyValue
from app.db.models.message import Message
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.stages.s4_inbox_triage import (
    DEFAULT_IGNORE_EMAILS,
    IGNORE_EMAILS_KEY,
    TriageResult,
    _add_ignore_email,
    _extract_body,
    _extract_sender,
    _extract_subject,
    _get_ignore_emails,
    _is_no_reply_sender,
    _is_platform_notification,
    triage_inbox,
)


@pytest.fixture(autouse=True)
def clean_ignore_list():
    """Remove the ignore-emails KV entry before and after each test."""
    with _db.SessionLocal() as session:
        session.query(KeyValue).filter_by(key=IGNORE_EMAILS_KEY).delete()
        session.commit()
    yield
    with _db.SessionLocal() as session:
        session.query(KeyValue).filter_by(key=IGNORE_EMAILS_KEY).delete()
        session.commit()


def _make_gmail_message(
    from_addr: str, subject: str, body: str, msg_id: str = "msg1", from_name: str = ""
) -> dict:
    from_header = f"{from_name} <{from_addr}>" if from_name else from_addr
    import base64

    encoded_body = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": from_header},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": encoded_body},
        },
    }


# ---------------------------------------------------------------------------
# Unit tests: email parsing helpers
# ---------------------------------------------------------------------------


class TestExtractSender:
    def test_simple_address(self):
        msg = _make_gmail_message("foo@bar.com", "hi", "body")
        name, addr = _extract_sender(msg)
        assert addr == "foo@bar.com"

    def test_named_sender(self):
        msg = _make_gmail_message("foo@bar.com", "hi", "body", from_name="John Doe")
        name, addr = _extract_sender(msg)
        assert name == "John Doe"
        assert addr == "foo@bar.com"

    def test_uppercase_normalized(self):
        msg = _make_gmail_message("FOO@BAR.COM", "hi", "body")
        _, addr = _extract_sender(msg)
        assert addr == "foo@bar.com"


class TestExtractSubject:
    def test_extracts_subject(self):
        msg = _make_gmail_message("a@b.com", "Hello World", "body")
        assert _extract_subject(msg) == "Hello World"


class TestExtractBody:
    def test_plain_text(self):
        msg = _make_gmail_message("a@b.com", "subj", "Hello there")
        assert _extract_body(msg) == "Hello there"


class TestIsNoReplySender:
    def test_noreply_in_address(self):
        assert _is_no_reply_sender("", "no-reply@example.com")
        assert _is_no_reply_sender("", "noreply@example.com")

    def test_noreply_in_name(self):
        assert _is_no_reply_sender("No-Reply", "alerts@example.com")

    def test_normal_sender(self):
        assert not _is_no_reply_sender("John", "john@example.com")


class TestIsPlatformNotification:
    def test_matches_new_message(self):
        assert _is_platform_notification("You have a new message from supplier", "")

    def test_matches_unread(self):
        assert _is_platform_notification("Unread message on Alibaba", "")

    def test_no_match(self):
        assert not _is_platform_notification("Your order has shipped", "tracking info")


# ---------------------------------------------------------------------------
# Unit tests: ignore list management
# ---------------------------------------------------------------------------


class TestIgnoreList:
    def test_seeds_defaults(self):
        emails = _get_ignore_emails()
        assert emails == set(DEFAULT_IGNORE_EMAILS)

    def test_persists_to_db(self):
        _get_ignore_emails()
        with _db.SessionLocal() as session:
            row = session.get(KeyValue, IGNORE_EMAILS_KEY)
            assert row is not None
            assert set(row.value) == set(DEFAULT_IGNORE_EMAILS)

    def test_add_new_address(self):
        _get_ignore_emails()
        result = _add_ignore_email("spam@example.com")
        assert "Added" in result
        emails = _get_ignore_emails()
        assert "spam@example.com" in emails

    def test_add_duplicate(self):
        _get_ignore_emails()
        _add_ignore_email("spam@example.com")
        result = _add_ignore_email("spam@example.com")
        assert "already" in result

    def test_add_normalizes_case(self):
        _get_ignore_emails()
        _add_ignore_email("SPAM@EXAMPLE.COM")
        emails = _get_ignore_emails()
        assert "spam@example.com" in emails


# ---------------------------------------------------------------------------
# Integration-style tests: triage pipeline (mocked Gmail + LLM)
# ---------------------------------------------------------------------------


@pytest.fixture
def supplier_thread():
    with _db.SessionLocal() as session:
        source = SourceProduct(
            url="https://www.kogan.com/au/buy/test-triage/",
            slug="test-triage",
            title="Test Triage Product",
            specs={"Display": {"Size": '75"'}},
        )
        session.add(source)
        session.flush()

        supplier = Supplier(
            name="Triage Supplier Co",
            platform="alibaba",
            profile_url="https://triage-supplier.alibaba.com",
        )
        session.add(supplier)
        session.flush()

        sup_product = SupplierProduct(
            source_product_id=source.id,
            supplier_id=supplier.id,
            platform="alibaba",
            product_url="https://www.alibaba.com/product-detail/triage_999.html",
            title="Triage Supplier TV",
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
        session.commit()
        session.refresh(thread)
        yield thread

        session.query(Message).filter_by(thread_id=thread.id).delete()
        session.query(SupplierThread).filter_by(id=thread.id).delete()
        session.query(SupplierProduct).filter_by(id=sup_product.id).delete()
        session.query(Supplier).filter_by(id=supplier.id).delete()
        session.query(SourceProduct).filter_by(id=source.id).delete()
        session.commit()


class TestTriageInbox:
    @pytest.fixture(autouse=True)
    def _no_platform_polling(self):
        with patch(
            "app.pipeline.stages.s4_inbox_triage._poll_platform_messages",
            return_value=0,
        ):
            yield

    def _mock_gmail(self, threads_and_messages: list[tuple[str, dict]]):
        """Build a mock GmailService with the given thread stubs and messages."""
        mock = MagicMock()
        mock.list_unread_threads.return_value = [
            {"id": tid} for tid, _ in threads_and_messages
        ]
        mock.get_thread.side_effect = lambda tid: {
            "messages": [msg] for t, msg in threads_and_messages if t == tid
        }
        return mock

    def test_auto_archives_ignored_sender(self):
        msg = _make_gmail_message("no-reply@google.com", "Security alert", "body")
        mock_gmail = self._mock_gmail([("t1", msg)])

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService", return_value=mock_gmail
        ):
            counts = triage_inbox()

        assert counts["archived_noise"] == 1
        mock_gmail.archive_thread.assert_called_once_with("t1")

    def test_flags_platform_notification(self):
        msg = _make_gmail_message(
            "no-reply@alibaba.com",
            "You have a new message from supplier",
            "Check your messages",
            from_name="No-Reply",
        )
        mock_gmail = self._mock_gmail([("t1", msg)])

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService",
            return_value=mock_gmail,
        ):
            counts = triage_inbox()

        assert counts["flagged_notification"] == 1
        assert counts["archived_noise"] == 1
        mock_gmail.archive_thread.assert_called_once_with("t1")
        mock_gmail.send_email.assert_not_called()

    def test_archives_noreply_non_notification(self):
        msg = _make_gmail_message(
            "no-reply@alibaba.com",
            "Your order has shipped",
            "Tracking number: 1234",
            from_name="No-Reply",
        )
        mock_gmail = self._mock_gmail([("t1", msg)])

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService", return_value=mock_gmail
        ):
            counts = triage_inbox()

        assert counts["archived_noise"] == 1
        mock_gmail.archive_thread.assert_called_once_with("t1")

    def test_known_gmail_thread_short_circuits(self, supplier_thread):
        """Messages on an already-linked Gmail thread skip LLM triage."""
        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, supplier_thread.id)
            thread.gmail_thread_id = "known_t1"
            session.commit()

        msg = _make_gmail_message(
            "sales@factory.cn",
            "Re: Follow up",
            "Updated pricing: $140 FOB",
            msg_id="gmsg_known",
        )
        mock_gmail = self._mock_gmail([("known_t1", msg)])

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService", return_value=mock_gmail
        ):
            counts = triage_inbox()

        assert counts["supplier_reply"] == 1
        mock_gmail.archive_thread.assert_called_once_with("known_t1")

        with _db.SessionLocal() as session:
            msgs = (
                session.query(Message)
                .filter_by(
                    thread_id=supplier_thread.id,
                    gmail_message_id="gmsg_known",
                )
                .all()
            )
            assert len(msgs) == 1

    def test_llm_triage_supplier_reply(self, supplier_thread):
        msg = _make_gmail_message(
            "sales@factory.cn",
            "Re: Inquiry about TV",
            "We can offer $150 FOB per unit, MOQ 500",
            msg_id="gmsg_100",
        )
        mock_gmail = self._mock_gmail([("t1", msg)])

        triage_result = TriageResult(
            action="reply_supplier",
            thread_id=supplier_thread.id,
            summary="Supplier quoted $150 FOB, MOQ 500",
            reason="Genuine supplier reply with pricing",
        )
        mock_run = MagicMock()
        mock_run.output = triage_result

        with (
            patch(
                "app.pipeline.stages.s4_inbox_triage.GmailService",
                return_value=mock_gmail,
            ),
            patch("app.pipeline.stages.s4_inbox_triage.Agent") as MockAgent,
        ):
            MockAgent.return_value.run_sync.return_value = mock_run
            counts = triage_inbox()

        assert counts["supplier_reply"] == 1
        # Spec: "archive every processed thread"
        mock_gmail.archive_thread.assert_called_once_with("t1")

        with _db.SessionLocal() as session:
            thread = session.get(SupplierThread, supplier_thread.id)
            assert thread.state == "AWAITING_REPLY"
            assert thread.gmail_thread_id == "t1"
            msgs = (
                session.query(Message)
                .filter_by(thread_id=supplier_thread.id, direction="inbound")
                .all()
            )
            assert len(msgs) == 1
            assert msgs[0].gmail_message_id == "gmsg_100"

    def test_llm_triage_archive(self):
        msg = _make_gmail_message(
            "marketing@randomco.com",
            "Special offer!",
            "Buy our products at a discount",
        )
        mock_gmail = self._mock_gmail([("t1", msg)])

        triage_result = TriageResult(
            action="archive",
            summary="Marketing spam",
            reason="Unsolicited marketing",
        )
        mock_run = MagicMock()
        mock_run.output = triage_result

        with (
            patch(
                "app.pipeline.stages.s4_inbox_triage.GmailService",
                return_value=mock_gmail,
            ),
            patch("app.pipeline.stages.s4_inbox_triage.Agent") as MockAgent,
        ):
            MockAgent.return_value.run_sync.return_value = mock_run
            counts = triage_inbox()

        assert counts["archived_noise"] == 1
        mock_gmail.archive_thread.assert_called_once_with("t1")

    def test_llm_triage_flag_human(self):
        msg = _make_gmail_message(
            "legal@supplier.com",
            "Business registration required",
            "Please provide your import licence",
        )
        mock_gmail = self._mock_gmail([("t1", msg)])

        triage_result = TriageResult(
            action="flag_human",
            reason="Legal/compliance request",
        )
        mock_run = MagicMock()
        mock_run.output = triage_result

        with (
            patch(
                "app.pipeline.stages.s4_inbox_triage.GmailService",
                return_value=mock_gmail,
            ),
            patch("app.pipeline.stages.s4_inbox_triage.Agent") as MockAgent,
        ):
            MockAgent.return_value.run_sync.return_value = mock_run
            counts = triage_inbox()

        assert counts["flagged"] == 1
        mock_gmail.archive_thread.assert_called_once_with("t1")

    def test_does_not_duplicate_messages(self, supplier_thread):
        msg = _make_gmail_message(
            "sales@factory.cn",
            "Re: Inquiry",
            "offer",
            msg_id="gmsg_dup",
        )
        # Pre-insert the message
        with _db.SessionLocal() as session:
            session.add(
                Message(
                    thread_id=supplier_thread.id,
                    gmail_message_id="gmsg_dup",
                    direction="inbound",
                    subject="Re: Inquiry",
                    body="offer",
                )
            )
            session.commit()

        mock_gmail = self._mock_gmail([("t1", msg)])
        triage_result = TriageResult(
            action="reply_supplier",
            thread_id=supplier_thread.id,
            summary="Duplicate",
            reason="Supplier reply",
        )
        mock_run = MagicMock()
        mock_run.output = triage_result

        with (
            patch(
                "app.pipeline.stages.s4_inbox_triage.GmailService",
                return_value=mock_gmail,
            ),
            patch("app.pipeline.stages.s4_inbox_triage.Agent") as MockAgent,
        ):
            MockAgent.return_value.run_sync.return_value = mock_run
            triage_inbox()

        with _db.SessionLocal() as session:
            msgs = (
                session.query(Message)
                .filter_by(
                    thread_id=supplier_thread.id,
                    gmail_message_id="gmsg_dup",
                )
                .all()
            )
            assert len(msgs) == 1

    def test_empty_inbox(self):
        mock_gmail = MagicMock()
        mock_gmail.list_unread_threads.return_value = []

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService", return_value=mock_gmail
        ):
            counts = triage_inbox()

        assert all(v == 0 for v in counts.values())

    def test_error_handling(self):
        mock_gmail = MagicMock()
        mock_gmail.list_unread_threads.return_value = [{"id": "t1"}]
        mock_gmail.get_thread.side_effect = RuntimeError("API error")

        with patch(
            "app.pipeline.stages.s4_inbox_triage.GmailService", return_value=mock_gmail
        ):
            counts = triage_inbox()

        assert counts["errors"] == 1
