"""
Integration tests for the Gmail service.
Runs against the real Gmail account — no mocks.
"""

import pytest

from app.services.gmail import GmailService

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gmail():
    return GmailService()


class TestAuthentication:
    def test_service_builds_successfully(self, gmail: GmailService):
        assert gmail.service is not None

    def test_can_get_profile(self, gmail: GmailService):
        profile = gmail.get_profile()
        assert "emailAddress" in profile
        assert "gmail.com" in profile["emailAddress"]


class TestInbox:
    def test_list_unread_threads(self, gmail: GmailService):
        threads = gmail.list_unread_threads()
        assert isinstance(threads, list)

    def test_get_thread(self, gmail: GmailService):
        threads = gmail.list_unread_threads()
        if not threads:
            pytest.skip("No unread threads to test")
        thread = gmail.get_thread(threads[0]["id"])
        assert "id" in thread
        assert "messages" in thread


class TestSendAndArchive:
    def test_send_email_to_self(self, gmail: GmailService):
        profile = gmail.get_profile()
        addr = profile["emailAddress"]
        msg = gmail.send_email(
            to=addr,
            subject="[TEST] GmailService integration test",
            body="Automated test — safe to delete.",
        )
        assert "id" in msg
        assert "threadId" in msg

    def test_reply_to_thread(self, gmail: GmailService):
        profile = gmail.get_profile()
        addr = profile["emailAddress"]
        # send an initial message, then reply to its thread
        original = gmail.send_email(
            to=addr,
            subject="[TEST] Reply thread test",
            body="Original message.",
        )
        reply = gmail.reply_to_thread(
            thread_id=original["threadId"],
            to=addr,
            subject="Re: [TEST] Reply thread test",
            body="Reply message.",
        )
        assert reply["threadId"] == original["threadId"]

    def test_archive_thread(self, gmail: GmailService):
        profile = gmail.get_profile()
        addr = profile["emailAddress"]
        msg = gmail.send_email(
            to=addr,
            subject="[TEST] Archive test",
            body="This thread will be archived.",
        )
        gmail.archive_thread(msg["threadId"])
        thread = gmail.get_thread(msg["threadId"])
        # after archiving, no message should have the INBOX label
        for m in thread["messages"]:
            assert "INBOX" not in m.get("labelIds", [])
