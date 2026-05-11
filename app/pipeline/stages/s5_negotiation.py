"""Stage 5: Process supplier replies and negotiate prices.

Two-step flow:
1. NegotiationExecutor (BatchExecutor) — fans out LLM negotiations across
   threads. Runs spec check, negotiate(), records quotes, updates state.
   Returns decisions with optional reply_text.
2. Reply sending — email replies sent directly, platform replies sent via
   PlatformReplyExecutor (BrowserFallbackExecutor) with per-thread auth.

_process_thread remains for backward compat / unit testing — it wraps
_negotiate_thread + send in one call.
"""

import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.base.config import scheduler_settings, settings
from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.quote import Quote
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.agents.match_agent import compare_products
from pydantic_ai.messages import BinaryContent

from app.pipeline.agents.negotiation_agent import (
    NegotiationAction,
    build_message_history,
    negotiate,
)
from app.pipeline.batch_executor import BatchExecutor
from app.pipeline.browser_executor import BrowserFallbackExecutor, FallbackResult
from app.services.gmail import GmailService
from app.pipeline.stages.s4_inbox_triage import _extract_sender

log = logging.getLogger(__name__)

# reply_fn(body) -> message_id or None
ReplyFn = Callable[[str], str | None]

# 2h–48h random delay for human-likeness (spec says 2h–2d)
REPLY_DELAY_MIN_HOURS = 2
REPLY_DELAY_MAX_HOURS = 48

SILENCE_DELAY_MIN_HOURS = 24
SILENCE_DELAY_MAX_HOURS = 72


def _fetch_pdf_attachments(message: Message) -> list[BinaryContent] | None:
    """Fetch PDF attachment bytes from Gmail for a message.

    Returns a list of BinaryContent for each PDF, or None if there are no
    attachments. Skips individual attachments that fail to download.
    """
    if not message.attachments:
        return None

    gmail = GmailService()
    results = []
    for att in message.attachments:
        if att.get("mime_type") != "application/pdf":
            continue
        try:
            data = gmail.get_attachment(att["gmail_message_id"], att["attachment_id"])
            results.append(BinaryContent(data=data, media_type="application/pdf"))
        except Exception:
            log.exception(
                "Failed to fetch attachment %s for message %s",
                att.get("filename"),
                message.id,
            )
    return results or None


def _random_delay(min_hours: int, max_hours: int) -> timedelta:
    seconds = random.randint(min_hours * 3600, max_hours * 3600)
    return timedelta(seconds=seconds)


def _get_ready_threads() -> list[int]:
    """Return IDs of threads that have an unprocessed supplier reply."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(
                SupplierThread.state.in_(["AWAITING_REPLY", "NEGOTIATING"]),
                (SupplierThread.respond_after.is_(None))
                | (SupplierThread.respond_after <= now),
            )
            .all()
        )
        return [t.id for t in threads]


def _run_spec_check(thread_id: int) -> bool:
    """Run spec check on the first supplier reply. Returns True if pass.

    Does NOT commit state changes — the caller is responsible for setting
    the final state after the full negotiation decision succeeds.
    """
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        source = thread.source_product
        supplier_product = thread.supplier_product

        latest_inbound = (
            session.query(Message)
            .filter_by(thread_id=thread_id, direction="inbound")
            .order_by(Message.sent_at.desc())
            .first()
        )
        if not latest_inbound:
            return False

        result = compare_products(
            reference_title=source.title,
            reference_specs=source.specs or {},
            candidate_title=supplier_product.title,
            candidate_details=supplier_product.specs or {},
        )

        log.info(
            "Spec check for thread %d: match=%s confidence=%.2f — %s",
            thread_id,
            result.is_match,
            result.confidence,
            result.reasoning[:100],
        )

        return result.is_match


def _record_quote(
    thread_id: int, price_usd: float | None, moq: int | None, lead_time: str | None
) -> None:
    if price_usd is None:
        return
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        round_number = thread.negotiation_rounds + 1
        session.add(
            Quote(
                thread_id=thread_id,
                round_number=round_number,
                price_usd=price_usd,
                moq=moq,
                lead_time=lead_time,
            )
        )
        session.commit()


def _record_negotiation_failure(thread_id: int) -> None:
    """Increment failure counter; move to UNPROCESSABLE if over threshold."""
    max_failures = scheduler_settings.MAX_NEGOTIATION_FAILURES
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if not thread:
            return
        thread.negotiation_failures = (thread.negotiation_failures or 0) + 1
        if thread.negotiation_failures >= max_failures:
            log.warning(
                "Thread %d failed negotiation %d times — marking UNPROCESSABLE",
                thread_id,
                thread.negotiation_failures,
            )
            thread.state = "UNPROCESSABLE"
        session.commit()


def _record_outbound(
    thread_id: int, message_id: str | None, channel: str, body: str
) -> None:
    with SessionLocal() as session:
        msg = Message(
            thread_id=thread_id,
            direction="outbound",
            channel=channel,
            body=body,
        )
        if channel == "email" and message_id:
            msg.gmail_message_id = message_id
        session.add(msg)
        session.commit()


def _send_and_record(
    thread_id: int, body: str, reply_fn: ReplyFn, channel: str
) -> bool:
    """Send a reply via reply_fn and record it. Returns True if sent."""
    msg_id = reply_fn(body)
    if msg_id is not None:
        _record_outbound(thread_id, msg_id, channel, body)
        return True
    return False


# ---------------------------------------------------------------------------
# Negotiation logic (LLM-only, no sending)
# ---------------------------------------------------------------------------


@dataclass
class NegotiationDecision:
    thread_id: int
    status: str
    reply_text: str | None = None


def _negotiate_thread(thread_id: int) -> NegotiationDecision:
    """Run negotiation for one thread.

    State changes are committed only after the full decision succeeds —
    if anything crashes mid-way, the thread stays in its original state
    and will be retried on the next run.
    """
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if not thread:
            return NegotiationDecision(thread_id=thread_id, status="not_found")

        state = thread.state
        negotiation_rounds = thread.negotiation_rounds
        product_title = thread.source_product.title

        messages = (
            session.query(Message)
            .filter_by(thread_id=thread_id)
            .order_by(Message.sent_at)
            .all()
        )
        # Detach from session for use outside
        for m in messages:
            session.expunge(m)

    # First supplier reply — run spec check
    if state == "AWAITING_REPLY" and negotiation_rounds == 0:
        passed = _run_spec_check(thread_id)
        if not passed:
            with SessionLocal() as session:
                thread = session.get(SupplierThread, thread_id)
                thread.state = "CLOSED"
                thread.negotiation_failures = 0
                session.commit()
            return NegotiationDecision(
                thread_id=thread_id,
                status="spec_check_fail",
                reply_text=(
                    "Thank you for your response. Unfortunately, the specifications "
                    "do not match our requirements. We appreciate your time."
                ),
            )
        # Passed — fall through to negotiation

    # Build message history and run negotiation
    inbound_messages = [m for m in messages if m.direction == "inbound"]
    if not inbound_messages:
        return NegotiationDecision(thread_id=thread_id, status="no_inbound")

    latest_inbound = inbound_messages[-1]
    message_history = build_message_history(messages[:-1]) if len(messages) > 1 else []

    pdf_attachments = _fetch_pdf_attachments(latest_inbound)

    result = negotiate(
        message_history=message_history,
        latest_supplier_message=latest_inbound.body,
        negotiation_rounds=negotiation_rounds,
        product_title=product_title,
        attachments=pdf_attachments,
    )

    log.info(
        "Thread %d negotiation: action=%s reasoning=%s",
        thread_id,
        result.action,
        result.reasoning[:100],
    )

    # Record any extracted pricing
    eq = result.extracted_quote
    _record_quote(thread_id, eq.price_usd, eq.moq, eq.lead_time)

    # Single commit for all state changes after the decision succeeds
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        thread.negotiation_failures = 0

        if result.action == NegotiationAction.REPLY:
            thread.state = "NEGOTIATING"
            thread.negotiation_rounds = negotiation_rounds + 1
            thread.respond_after = datetime.now(timezone.utc) + _random_delay(
                REPLY_DELAY_MIN_HOURS,
                REPLY_DELAY_MAX_HOURS,
            )
            session.commit()
            return NegotiationDecision(
                thread_id=thread_id, status="replied", reply_text=result.reply_text
            )

        elif result.action == NegotiationAction.SILENCE:
            thread.respond_after = datetime.now(timezone.utc) + _random_delay(
                SILENCE_DELAY_MIN_HOURS,
                SILENCE_DELAY_MAX_HOURS,
            )
            session.commit()
            return NegotiationDecision(thread_id=thread_id, status="silence")

        elif result.action == NegotiationAction.CLOSE:
            if eq.price_usd is not None:
                thread.state = "FINAL_PRICE_LOGGED"
            else:
                thread.state = "CLOSED"
            session.commit()
            return NegotiationDecision(
                thread_id=thread_id, status="closed", reply_text=result.reply_text
            )

        session.commit()
    return NegotiationDecision(thread_id=thread_id, status="unknown")


def _process_thread(thread_id: int, reply_fn: ReplyFn, channel: str) -> str:
    """Full thread processing: negotiate then send. For backward compat / testing."""
    decision = _negotiate_thread(thread_id)
    if decision.reply_text:
        _send_and_record(thread_id, decision.reply_text, reply_fn, channel)
    return decision.status


# ---------------------------------------------------------------------------
# Email reply helpers
# ---------------------------------------------------------------------------


def _get_supplier_email(gmail: GmailService, gmail_thread_id: str) -> str | None:
    """Extract the supplier's email from the Gmail thread."""
    thread_data = gmail.get_thread(gmail_thread_id)
    for msg in reversed(thread_data.get("messages", [])):
        _, sender_email = _extract_sender(msg)
        if sender_email and "sourcing_agent" not in sender_email:
            return sender_email
    return None


def _make_email_reply_fn(gmail: GmailService, gmail_thread_id: str) -> ReplyFn:
    """Build a reply_fn that sends via Gmail."""

    def reply_fn(body: str) -> str | None:
        supplier_email = _get_supplier_email(gmail, gmail_thread_id)
        if not supplier_email:
            log.warning(
                "Could not find supplier email for Gmail thread %s", gmail_thread_id
            )
            return None
        result = gmail.reply_to_thread(
            gmail_thread_id, supplier_email, "Re: Product Inquiry", body
        )
        return result.get("id", "")

    return reply_fn


# ---------------------------------------------------------------------------
# Thread metadata
# ---------------------------------------------------------------------------


def _prepare_thread_metadata(thread_ids: list[int]) -> list[dict]:
    """Load metadata for all threads up front to avoid DB access during pool."""
    metadata = []
    with SessionLocal() as session:
        for thread_id in thread_ids:
            thread = session.get(SupplierThread, thread_id)
            metadata.append(
                {
                    "thread_id": thread_id,
                    "channel": thread.channel or "email",
                    "gmail_thread_id": thread.gmail_thread_id,
                    "platform_thread_url": thread.platform_thread_url,
                    "platform_name": thread.supplier_product.platform,
                }
            )
    return metadata


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


class NegotiationExecutor(BatchExecutor):
    """Fan out LLM negotiations across threads."""

    @property
    def stage(self) -> str:
        return "s5_negotiation"

    @property
    def action(self) -> str:
        return "negotiate"

    def __init__(self):
        self._thread_ids = _get_ready_threads()

    def get_work_items(self) -> dict[str, list[dict]]:
        if not self._thread_ids:
            return {}
        meta_list = _prepare_thread_metadata(self._thread_ids)
        grouped: dict[str, list[dict]] = {}
        for meta in meta_list:
            grouped.setdefault(meta["platform_name"], []).append(meta)
        return grouped

    def _process_batch(
        self, batch: list[dict], group_key: str
    ) -> list[tuple[dict, NegotiationDecision | None]]:
        results: list[tuple[dict, NegotiationDecision | None]] = []
        for item in batch:
            threading.current_thread().name = self.thread_label(item)
            thread_id = item["thread_id"]

            # Skip threads with no reply channel
            if item["channel"] == "email" and not item["gmail_thread_id"]:
                log.warning("Thread %d has no gmail_thread_id, skipping", thread_id)
                results.append((item, NegotiationDecision(thread_id, "no_channel")))
                continue
            if item["channel"] != "email" and not item["platform_thread_url"]:
                log.warning("Thread %d has no platform_thread_url, skipping", thread_id)
                results.append((item, NegotiationDecision(thread_id, "no_channel")))
                continue

            try:
                decision = _negotiate_thread(thread_id)
                results.append((item, decision))
            except Exception:
                log.exception("Error negotiating thread %d", thread_id)
                _record_negotiation_failure(thread_id)
                results.append((item, None))
        return results

    def thread_label(self, item: dict) -> str:
        return f"negotiate-{item['thread_id']}"


class PlatformReplyExecutor(BrowserFallbackExecutor):
    """Send platform replies via browser with per-thread auth."""

    def __init__(self, reply_items: dict[str, list[dict]]):
        self._items = reply_items

    @property
    def stage(self) -> str:
        return "s5_negotiation"

    @property
    def action(self) -> str:
        return "send_reply"

    def get_work_items(self) -> dict[str, list[dict]]:
        return self._items

    def deterministic_action(self, item: dict, page, platform, context_id: str):
        success = platform.send_platform_reply(
            page, item["conversation_url"], item["reply_text"]
        )
        if not success:
            raise RuntimeError("Deterministic send returned False")
        return f"platform_{item['thread_id']}"

    def agent_fallback(self, item: dict, session_id: str, platform) -> FallbackResult:
        from app.pipeline.agents.platform_message_agent import (
            ReplyStatus,
            send_reply_via_agent,
        )

        result = send_reply_via_agent(
            session_id,
            item["conversation_url"],
            item["reply_text"],
            platform_prompt=platform.messaging_agent_prompt,
        )
        return FallbackResult(
            success=(result.status == ReplyStatus.SENT),
            result=f"platform_{item['thread_id']}",
        )

    def on_success(self, item: dict, result) -> None:
        _record_outbound(item["thread_id"], result, "platform", item["reply_text"])

    def thread_label(self, item: dict) -> str:
        return f"reply-{item['thread_id']}"

    def get_thread_id(self, item: dict) -> int | None:
        return item.get("thread_id")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def process_negotiations() -> dict:
    """Process all threads ready for negotiation.

    Step 1: NegotiationExecutor fans out LLM negotiations.
    Step 2a: Email replies sent directly via Gmail.
    Step 2b: Platform replies sent via PlatformReplyExecutor (browser).

    Returns a summary dict with counts per outcome.
    """
    # Step 1: Negotiate
    results = NegotiationExecutor().execute()
    if not results:
        log.info("No threads ready for negotiation")
        return {}

    log.info("Negotiated %d threads", len(results))

    counts: dict[str, int] = {}
    email_replies: list[tuple[dict, NegotiationDecision]] = []
    platform_replies: dict[str, list[dict]] = {}

    for meta, decision in results:
        if decision is None:
            counts["error"] = counts.get("error", 0) + 1
            continue

        counts[decision.status] = counts.get(decision.status, 0) + 1
        log.info("Thread %d → %s", decision.thread_id, decision.status)

        if decision.reply_text:
            if meta["channel"] == "email":
                email_replies.append((meta, decision))
            elif meta["platform_thread_url"]:
                platform_replies.setdefault(meta["platform_name"], []).append(
                    {
                        "thread_id": decision.thread_id,
                        "reply_text": decision.reply_text,
                        "conversation_url": meta["platform_thread_url"],
                    }
                )

    # Step 2a: Send email replies
    if email_replies:
        gmail = GmailService()
        for meta, decision in email_replies:
            if not meta["gmail_thread_id"]:
                continue
            reply_fn = _make_email_reply_fn(gmail, meta["gmail_thread_id"])
            msg_id = reply_fn(decision.reply_text)
            if msg_id is not None:
                _record_outbound(
                    decision.thread_id, msg_id, "email", decision.reply_text
                )

    # Step 2b: Send platform replies
    if platform_replies:
        PlatformReplyExecutor(platform_replies).execute()

    log.info("Negotiation processing complete: %s", counts)
    return counts
