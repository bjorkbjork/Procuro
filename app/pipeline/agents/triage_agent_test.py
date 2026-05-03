"""Integration tests for triage LLM classification quality.

These tests hit real Bedrock to validate that the LLM correctly classifies
different email types. The DB query for active threads is patched with a
canned summary, but the LLM call goes through to the real model.
"""

from unittest.mock import patch

import pytest

from app.pipeline.stages.s4_inbox_triage import TriageResult, _triage_with_llm

MOCK_THREADS_SUMMARY = (
    "- Thread 42 [OUTREACH_SENT]: Shenzhen Display Co"
    " — 75 inch QLED Smart TV 4K UHD (gmail_thread: none)\n"
    "- Thread 99 [NEGOTIATING]: Guangzhou Electronics Ltd"
    " — Air Fryer 45L Digital (gmail_thread: gt_123)"
)


@pytest.mark.integration
class TestTriageAgent:
    """Each test patches _get_active_threads_summary but lets the real LLM
    classify the email via Bedrock."""

    @pytest.fixture(autouse=True)
    def patch_threads_summary(self):
        with patch(
            "app.pipeline.stages.s4_inbox_triage._get_active_threads_summary",
            return_value=MOCK_THREADS_SUMMARY,
        ):
            yield

    def test_genuine_supplier_reply(self):
        result = _triage_with_llm(
            sender_name="Sales Dept",
            sender_email="sales@shenzhen-factory.com",
            subject="Re: Product Inquiry — 75 inch QLED TV",
            body=(
                "Dear Tom,\n\n"
                "Thank you for your inquiry. We can offer the 75 inch QLED "
                "Smart TV 4K UHD at the following pricing:\n\n"
                "FOB Shenzhen: $185 per unit\n"
                "MOQ: 500 units\n"
                "Lead time: 30-45 days after deposit\n\n"
                "Please let us know if you have further questions.\n\n"
                "Best regards,\n"
                "Wang Lei\n"
                "Shenzhen Display Co., Ltd."
            ),
        )

        assert isinstance(result, TriageResult)
        assert result.action == "reply_supplier"
        assert result.thread_id == 42

    def test_marketing_spam_archived(self):
        result = _triage_with_llm(
            sender_name="Alibaba Deals",
            sender_email="marketing@alibaba-deals.com",
            subject="Special Offer! 50% Off All Products",
            body=(
                "Dear Valued Customer,\n\n"
                "Don't miss our BIGGEST SALE of the year! Up to 50% off on "
                "all electronics, home appliances, and more. Limited time "
                "only!\n\n"
                "Click here to browse our deals: https://alibaba-deals.com/sale\n\n"
                "Unsubscribe: https://alibaba-deals.com/unsub\n"
            ),
        )

        assert isinstance(result, TriageResult)
        assert result.action == "archive"

    def test_legal_request_flagged(self):
        result = _triage_with_llm(
            sender_name="China Trade Compliance Office",
            sender_email="compliance@trade-authority.gov.cn",
            subject="Business Registration Required for Import",
            body=(
                "Dear Sir/Madam,\n\n"
                "Our records indicate that your company is sourcing products "
                "from Chinese manufacturers for import into Australia. Under "
                "current regulations, all foreign buyers must register with "
                "the local trade authority and provide a valid import license.\n\n"
                "Please submit the following documents within 30 days:\n"
                "1. Business registration certificate\n"
                "2. Import license\n"
                "3. Proof of Australian business address\n\n"
                "Failure to comply may result in suspension of trade activities.\n\n"
                "Regards,\n"
                "Compliance Department"
            ),
        )

        assert isinstance(result, TriageResult)
        assert result.action == "flag_human"

    def test_ambiguous_email_flagged(self):
        result = _triage_with_llm(
            sender_name="Info",
            sender_email="info@supplier-unknown.com",
            subject="Regarding your recent order",
            body=(
                "Hello,\n\n"
                "We are writing to you about your recent order. Please get "
                "back to us at your earliest convenience.\n\n"
                "Thank you."
            ),
        )

        assert isinstance(result, TriageResult)
        # Ambiguous emails should not be classified as supplier replies —
        # either flag_human or archive is acceptable
        assert result.action in ("flag_human", "archive")

    def test_non_english_supplier_reply(self):
        result = _triage_with_llm(
            sender_name="Wang",
            sender_email="wang@factory.cn",
            subject="Re: Inquiry",
            body=(
                "Tom 你好，\n\n"
                "感谢您的询价。我们的报价如下：\n\n"
                "FOB深圳价格：每台185美元\n"
                "最低起订量：500台\n"
                "交货期：收到定金后30-45天\n\n"
                "如有任何问题，请随时联系我们。\n\n"
                "王磊\n"
                "广州电子有限公司"
            ),
        )

        assert isinstance(result, TriageResult)
        assert result.action == "reply_supplier"
        assert result.thread_id == 99

    def test_platform_notification_archived(self):
        result = _triage_with_llm(
            sender_name="Alibaba Notification",
            sender_email="notification@service.alibaba.com",
            subject="Your supplier has sent you a message",
            body=(
                "Dear User,\n\n"
                "You have received a new message from a supplier on Alibaba. "
                "Please log in to your Alibaba account to view and respond "
                "to the message.\n\n"
                "Log in now: https://message.alibaba.com/inbox\n\n"
                "This is an automated notification from Alibaba.com."
            ),
        )

        assert isinstance(result, TriageResult)
        assert result.action == "archive"
