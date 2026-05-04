"""Stage 5: Process supplier replies and negotiate prices.

Flow per thread:
1. Pick up threads in AWAITING_REPLY where respond_after has passed (or is null).
2. First reply from a supplier triggers a spec check via the match agent.
   - SPEC_CHECK_FAIL → send polite decline, close thread.
   - SPEC_CHECK_PASS → fall through to negotiation.
3. Run the negotiation agent with full conversation history.
4. Act on the result:
   - reply  → send reply, record quote, set respond_after delay, NEGOTIATING
   - silence → set respond_after delay (no reply sent)
   - close  → send final reply, record quote, FINAL_PRICE_LOGGED or CLOSED

Replies are sent via a channel-agnostic reply_fn closure — _process_thread
never knows whether it's Gmail or platform messaging.
"""

import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.pipeline.agents.match_agent import compare_products
from app.pipeline.agents.negotiation_agent import (
    NegotiationAction,
    build_message_history,
    negotiate,
)
from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.quote import Quote
from app.db.models.supplier_thread import SupplierThread
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.base.config import settings
from app.pipeline.browser_executor import FallbackResult, run_with_browser_fallback
from app.services.browser import authenticate_platform
from app.services.gmail import GmailService
from app.services.platforms import get_platforms
from app.pipeline.stages.s4_inbox_triage import _extract_sender

log = logging.getLogger(__name__)

# reply_fn(body) -> message_id or None
ReplyFn = Callable[[str], str | None]

# 2h–48h random delay for human-likeness (spec says 2h–2d)
REPLY_DELAY_MIN_HOURS = 2
REPLY_DELAY_MAX_HOURS = 48

SILENCE_DELAY_MIN_HOURS = 24
SILENCE_DELAY_MAX_HOURS = 72


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
    """Run spec check on the first supplier reply. Returns True if pass."""
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

        if result.is_match:
            thread.state = "SPEC_CHECK_PASS"
        else:
            thread.state = "SPEC_CHECK_FAIL"
        session.commit()

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


def _process_thread(thread_id: int, reply_fn: ReplyFn, channel: str) -> str:
    """Process a single thread. Returns a status string for logging."""
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if not thread:
            return "not_found"

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
            _send_and_record(
                thread_id,
                "Thank you for your response. Unfortunately, the specifications "
                "do not match our requirements. We appreciate your time.",
                reply_fn,
                channel,
            )
            with SessionLocal() as session:
                thread = session.get(SupplierThread, thread_id)
                thread.state = "CLOSED"
                session.commit()
            return "spec_check_fail"
        # Passed — fall through to negotiation

    # Build message history and run negotiation
    inbound_messages = [m for m in messages if m.direction == "inbound"]
    if not inbound_messages:
        return "no_inbound"

    latest_inbound = inbound_messages[-1]
    message_history = build_message_history(messages[:-1]) if len(messages) > 1 else []

    result = negotiate(
        message_history=message_history,
        latest_supplier_message=latest_inbound.body,
        negotiation_rounds=negotiation_rounds,
        product_title=product_title,
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

    if result.action == NegotiationAction.REPLY:
        if result.reply_text:
            _send_and_record(thread_id, result.reply_text, reply_fn, channel)

        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            thread.state = "NEGOTIATING"
            thread.negotiation_rounds = negotiation_rounds + 1
            thread.respond_after = datetime.now(timezone.utc) + _random_delay(
                REPLY_DELAY_MIN_HOURS,
                REPLY_DELAY_MAX_HOURS,
            )
            session.commit()
        return "replied"

    elif result.action == NegotiationAction.SILENCE:
        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            thread.respond_after = datetime.now(timezone.utc) + _random_delay(
                SILENCE_DELAY_MIN_HOURS,
                SILENCE_DELAY_MAX_HOURS,
            )
            session.commit()
        return "silence"

    elif result.action == NegotiationAction.CLOSE:
        if result.reply_text:
            _send_and_record(thread_id, result.reply_text, reply_fn, channel)

        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            if eq.price_usd is not None:
                thread.state = "FINAL_PRICE_LOGGED"
            else:
                thread.state = "CLOSED"
            session.commit()
        return "closed"

    return "unknown"


# ---------------------------------------------------------------------------
# Reply function builders
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


def _make_platform_reply_fn(
    platform, context_id: str, conversation_url: str, thread_id: int
) -> ReplyFn:
    """Build a reply_fn that sends via platform messaging.

    Tries deterministic Playwright first, falls back to the LLM agent.
    Uses run_with_browser_fallback for consistent lifecycle and event recording.
    """
    from app.pipeline.agents.platform_message_agent import (
        ReplyStatus,
        send_reply_via_agent,
    )

    def reply_fn(body: str) -> str | None:
        msg_id = f"platform_{id(body)}"

        def deterministic(page):
            success = platform.send_platform_reply(page, conversation_url, body)
            if not success:
                raise RuntimeError("Deterministic send returned False")
            return msg_id

        def fallback(session_id):
            result = send_reply_via_agent(
                session_id,
                conversation_url,
                body,
                platform_prompt=platform.messaging_agent_prompt,
            )
            return FallbackResult(
                success=(result.status == ReplyStatus.SENT),
                result=msg_id,
            )

        return run_with_browser_fallback(
            context_id,
            deterministic,
            fallback,
            stage="s5_negotiation",
            action="send_reply",
            supplier_thread_id=thread_id,
        )

    return reply_fn


# ---------------------------------------------------------------------------
# Main entry point
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


def process_negotiations() -> dict:
    """Process all threads ready for negotiation concurrently.

    Returns a summary dict with counts per outcome.
    """
    thread_ids = _get_ready_threads()

    if not thread_ids:
        log.info("No threads ready for negotiation")
        return {}

    log.info("Processing %d threads for negotiation", len(thread_ids))

    # Pre-load thread metadata and authenticate platforms before the pool
    thread_meta = _prepare_thread_metadata(thread_ids)
    gmail = GmailService()
    platform_objs = {p.platform.value: p for p in get_platforms()}
    platform_contexts: dict[str, str] = {}

    # Pre-authenticate all needed platforms (avoids races in the pool)
    for meta in thread_meta:
        if meta["channel"] != "email":
            pname = meta["platform_name"]
            if pname not in platform_contexts and pname in platform_objs:
                try:
                    platform_contexts[pname] = authenticate_platform(
                        platform_objs[pname]
                    )
                except Exception:
                    log.exception("Could not authenticate on %s", pname)

    counts: dict[str, int] = {}
    lock = __import__("threading").Lock()

    def _process_one(meta: dict) -> str:
        thread_id = meta["thread_id"]
        channel = meta["channel"]

        if channel == "email":
            if not meta["gmail_thread_id"]:
                log.warning("Thread %d has no gmail_thread_id, skipping", thread_id)
                return "no_channel"
            reply_fn = _make_email_reply_fn(gmail, meta["gmail_thread_id"])
        else:
            if not meta["platform_thread_url"]:
                log.warning("Thread %d has no platform_thread_url, skipping", thread_id)
                return "no_channel"
            platform = platform_objs.get(meta["platform_name"])
            if not platform:
                log.warning(
                    "No platform for '%s', skipping thread %d",
                    meta["platform_name"],
                    thread_id,
                )
                return "no_channel"
            ctx = platform_contexts.get(meta["platform_name"])
            if not ctx:
                log.warning(
                    "No auth context for '%s', skipping thread %d",
                    meta["platform_name"],
                    thread_id,
                )
                return "no_channel"
            reply_fn = _make_platform_reply_fn(
                platform, ctx, meta["platform_thread_url"], thread_id
            )

        return _process_thread(thread_id, reply_fn, channel)

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
        futures = {pool.submit(_process_one, meta): meta for meta in thread_meta}
        for future in as_completed(futures):
            meta = futures[future]
            try:
                status = future.result()
                with lock:
                    counts[status] = counts.get(status, 0) + 1
                log.info("Thread %d → %s", meta["thread_id"], status)
            except Exception:
                log.exception("Error processing thread %d", meta["thread_id"])
                with lock:
                    counts["error"] = counts.get("error", 0) + 1

    log.info("Negotiation processing complete: %s", counts)
    return counts
