"""Stage 5: Process supplier replies and negotiate prices.

Flow per thread:
1. Pick up threads in AWAITING_REPLY where respond_after has passed (or is null).
2. First reply from a supplier triggers a spec check via the match agent.
   - SPEC_CHECK_FAIL → send polite decline, close thread.
   - SPEC_CHECK_PASS → fall through to negotiation.
3. Run the negotiation agent with full conversation history.
4. Act on the result:
   - reply  → send via Gmail, record quote, set respond_after delay, NEGOTIATING
   - silence → set respond_after delay (no reply sent)
   - close  → send final reply, record quote, FINAL_PRICE_LOGGED or CLOSED
"""

import logging
import random
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
from app.services.gmail import GmailService
from app.pipeline.stages.s4_inbox_triage import _extract_sender

log = logging.getLogger(__name__)

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


def _get_supplier_email(gmail: GmailService, gmail_thread_id: str) -> str | None:
    """Extract the supplier's email from the Gmail thread."""
    thread_data = gmail.get_thread(gmail_thread_id)
    for msg in reversed(thread_data.get("messages", [])):
        _, sender_email = _extract_sender(msg)
        if sender_email and "sourcing_agent" not in sender_email:
            return sender_email
    return None


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
    thread_id: int, gmail_message_id: str, subject: str, body: str
) -> None:
    with SessionLocal() as session:
        session.add(
            Message(
                thread_id=thread_id,
                gmail_message_id=gmail_message_id,
                direction="outbound",
                subject=subject,
                body=body,
            )
        )
        session.commit()


def _process_thread(thread_id: int, gmail: GmailService) -> str:
    """Process a single thread. Returns a status string for logging."""
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if not thread:
            return "not_found"

        state = thread.state
        gmail_thread_id = thread.gmail_thread_id
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

    if not gmail_thread_id:
        log.warning("Thread %d has no gmail_thread_id, skipping", thread_id)
        return "no_gmail_thread"

    # First supplier reply — run spec check
    if state == "AWAITING_REPLY" and negotiation_rounds == 0:
        passed = _run_spec_check(thread_id)
        if not passed:
            supplier_email = _get_supplier_email(gmail, gmail_thread_id)
            if supplier_email:
                reply = gmail.reply_to_thread(
                    gmail_thread_id,
                    supplier_email,
                    "Re: Product Inquiry",
                    "Thank you for your response. Unfortunately, the specifications "
                    "do not match our requirements. We appreciate your time.",
                )
                _record_outbound(
                    thread_id,
                    reply.get("id", ""),
                    "Re: Product Inquiry",
                    "Spec check decline",
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

    supplier_email = _get_supplier_email(gmail, gmail_thread_id)

    if result.action == NegotiationAction.REPLY:
        if supplier_email and result.reply_text:
            reply = gmail.reply_to_thread(
                gmail_thread_id,
                supplier_email,
                "Re: Product Inquiry",
                result.reply_text,
            )
            _record_outbound(
                thread_id,
                reply.get("id", ""),
                "Re: Product Inquiry",
                result.reply_text,
            )

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
        if supplier_email and result.reply_text:
            reply = gmail.reply_to_thread(
                gmail_thread_id,
                supplier_email,
                "Re: Product Inquiry",
                result.reply_text,
            )
            _record_outbound(
                thread_id,
                reply.get("id", ""),
                "Re: Product Inquiry",
                result.reply_text,
            )

        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            if eq.price_usd is not None:
                thread.state = "FINAL_PRICE_LOGGED"
            else:
                thread.state = "CLOSED"
            session.commit()
        return "closed"

    return "unknown"


def process_negotiations() -> dict:
    """Process all threads ready for negotiation.

    Returns a summary dict with counts per outcome.
    """
    gmail = GmailService()
    thread_ids = _get_ready_threads()

    if not thread_ids:
        log.info("No threads ready for negotiation")
        return {}

    log.info("Processing %d threads for negotiation", len(thread_ids))
    counts: dict[str, int] = {}

    for thread_id in thread_ids:
        try:
            status = _process_thread(thread_id, gmail)
            counts[status] = counts.get(status, 0) + 1
            log.info("Thread %d → %s", thread_id, status)
        except Exception:
            log.exception("Error processing thread %d", thread_id)
            counts["error"] = counts.get("error", 0) + 1

    log.info("Negotiation processing complete: %s", counts)
    return counts
