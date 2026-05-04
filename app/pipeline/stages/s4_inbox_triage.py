"""Stage 4: Monitor Gmail inbox and platform messaging, triage supplier replies.

Gmail triage — three-tier filter:
1. Auto-archive known noise (AWS, Google, Alibaba notifications, etc.)
2. No-reply senders: platform notification emails are archived (the platform
   polling below handles the actual messages directly)
3. Everything else goes to the LLM triage agent for classification

Platform polling — for each platform with active threads:
1. Authenticate and open the messaging inbox
2. Deterministic Playwright read, agent fallback on failure
3. Match messages to supplier threads by supplier name
4. Record inbound messages with channel='platform'

The ignore-email list is stored in the keyvalue table and the LLM agent can
add new addresses to it via a tool."""

import email.utils
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.base.config import model_settings, settings
from app.base.llm import Agent, get_model
from app.db.database import SessionLocal
from app.db.models.keyvalue import KeyValue
from app.db.models.message import Message
from app.db.models.supplier import Supplier
from app.db.models.supplier_thread import SupplierThread
from app.pipeline.browser_executor import BrowserFallbackExecutor, FallbackResult
from app.services.browser import BrowserSession, authenticate_platform
from app.services.gmail import GmailService
from app.services.platforms import get_platforms
from app.services.platforms.platform import SupplierPlatform

log = logging.getLogger(__name__)

IGNORE_EMAILS_KEY = "ignore_emails"

DEFAULT_IGNORE_EMAILS = [
    "no-reply@verify.signin.aws",
    "no-reply-aws@amazon.com",
    "no-reply@google.com",
    "member@notice.alibaba.com",
    "no-reply@marketplace.aws",
    "aws-marketing-email-replies@amazon.com",
    "hello@browserbase.com",
]

PLATFORM_NOTIFICATION_PATTERNS = [
    re.compile(r"you have a new message", re.IGNORECASE),
    re.compile(r"new inquiry response", re.IGNORECASE),
    re.compile(r"supplier.*(?:replied|responded|sent)", re.IGNORECASE),
    re.compile(r"message from .* on (?:alibaba|globalsources)", re.IGNORECASE),
    re.compile(r"unread message", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Ignore-list helpers (KV-backed)
# ---------------------------------------------------------------------------


def _get_ignore_emails() -> set[str]:
    with SessionLocal() as session:
        row = session.get(KeyValue, IGNORE_EMAILS_KEY)
        if row is None:
            row = KeyValue(key=IGNORE_EMAILS_KEY, value=DEFAULT_IGNORE_EMAILS)
            session.add(row)
            session.commit()
            return set(DEFAULT_IGNORE_EMAILS)
        return set(row.value)


def _add_ignore_email(address: str) -> str:
    address = address.strip().lower()
    with SessionLocal() as session:
        row = session.get(KeyValue, IGNORE_EMAILS_KEY)
        if row is None:
            row = KeyValue(key=IGNORE_EMAILS_KEY, value=[address])
            session.add(row)
        else:
            current = list(row.value)
            if address in current:
                return f"{address} is already in the ignore list"
            current.append(address)
            row.value = current
        session.commit()
    log.info("Added %s to ignore list", address)
    return f"Added {address} to ignore list"


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------


def _extract_sender(msg: dict) -> tuple[str, str]:
    """Return (sender_name, sender_email) from a Gmail message."""
    headers = {
        h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])
    }
    from_header = headers.get("from", "")
    name, addr = email.utils.parseaddr(from_header)
    return name, addr.lower()


def _extract_subject(msg: dict) -> str:
    headers = {
        h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])
    }
    return headers.get("subject", "")


def _extract_body(msg: dict) -> str:
    """Extract plain-text body from a Gmail message."""
    payload = msg.get("payload", {})

    def _decode_part(part: dict) -> str:
        import base64

        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    if payload.get("mimeType") == "text/plain":
        return _decode_part(payload)

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            return _decode_part(part)
        for sub in part.get("parts", []):
            if sub.get("mimeType") == "text/plain":
                return _decode_part(sub)

    return ""


def _is_no_reply_sender(name: str, addr: str) -> bool:
    return "no-reply" in addr or "noreply" in addr or "no-reply" in name.lower()


def _is_platform_notification(subject: str, body: str) -> bool:
    text = f"{subject} {body}"
    return any(p.search(text) for p in PLATFORM_NOTIFICATION_PATTERNS)


def _alert_maintainer(
    gmail: GmailService,
    sender_email: str,
    subject: str,
    body: str,
    *,
    tag: str = "Platform Alert",
    preamble: str = "A supplier may have responded via on-platform messaging.",
) -> None:
    """Email the maintainer about something that needs attention."""
    maintainer = settings.MAINTAINER_EMAIL_ADDRESS
    if not maintainer:
        log.warning("MAINTAINER_EMAIL_ADDRESS not set — skipping alert")
        return
    gmail.send_email(
        to=maintainer,
        subject=f"[{tag}] {subject}",
        body=(
            f"{preamble}\n\n"
            f"From: {sender_email}\n"
            f"Subject: {subject}\n\n"
            f"{body}"
        ),
    )


# ---------------------------------------------------------------------------
# LLM triage agent
# ---------------------------------------------------------------------------


class TriageResult(BaseModel):
    action: str = Field(
        description="One of: 'reply_supplier', 'archive', 'add_to_ignore_list', 'flag_human'",
    )
    thread_id: int | None = Field(
        default=None,
        description="Matched supplier_thread ID if this is a supplier reply",
    )
    summary: str = Field(
        default="",
        description="Brief summary of the email content",
    )
    reason: str = Field(
        default="",
        description="Why this action was chosen",
    )


TRIAGE_SYSTEM_PROMPT = """\
You are an email triage agent for the agent, a procurement specialist at a \
leading Australian distributor. You process incoming emails related to \
supplier sourcing on Alibaba and GlobalSources.

You will receive an email (sender, subject, body) and a list of active \
supplier threads with their states.

Classify each email into one of these actions:

1. **reply_supplier** — This is a genuine reply from a supplier about pricing, \
   specs, or negotiation. Set thread_id to the matching supplier thread. \
   Include a summary of what the supplier said (price, MOQ, lead time, etc.)

2. **archive** — This is one-off noise that doesn't warrant permanently \
   ignoring the sender (e.g. a one-time notification, an irrelevant but \
   potentially legitimate sender).

3. **add_to_ignore_list** — This is noise AND the sender should be permanently \
   auto-archived in the future. Use for: recurring newsletters, automated \
   billing/invoice senders, platform marketing, SaaS notifications, and any \
   address that will never send sourcing-relevant content. Do NOT use for \
   actual supplier email addresses.

4. **flag_human** — You're unsure what this is, or it requires human attention \
   (e.g. legal request, account issue, something unexpected).

Rules:
- If you can match the email to a specific supplier thread (by company name, \
  product, or prior conversation), set thread_id.
- If a supplier writes in a non-English language, still classify it as \
  reply_supplier — the negotiation agent will handle the language barrier.
- Platform notifications about new messages should be flagged for human \
  review — the supplier may be responding on-platform only.
- When in doubt, flag_human rather than archiving a real supplier reply."""


def _get_active_threads_summary() -> str:
    """Build a summary of active supplier threads for the LLM."""
    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(SupplierThread.state.notin_(["NEW", "CLOSED", "UNPROCESSABLE"]))
            .all()
        )
        if not threads:
            return "No active supplier threads."

        lines = []
        for t in threads:
            sp = t.supplier_product
            supplier = t.supplier
            lines.append(
                f"- Thread {t.id} [{t.state}]: "
                f"{supplier.name} — {sp.title[:80]} "
                f"(gmail_thread: {t.gmail_thread_id or 'none'})"
            )
        return "\n".join(lines)


def _triage_with_llm(
    sender_name: str,
    sender_email: str,
    subject: str,
    body: str,
) -> TriageResult:
    """Send an email to the LLM triage agent for classification."""
    agent = Agent(
        model=get_model(model_settings.CHEAP),
        name="inbox_triage_agent",
        system_prompt=TRIAGE_SYSTEM_PROMPT,
        output_type=TriageResult,
        retries=2,
    )

    threads_summary = _get_active_threads_summary()

    prompt = (
        f"## Active supplier threads\n{threads_summary}\n\n"
        f"## Email to triage\n"
        f"From: {sender_name} <{sender_email}>\n"
        f"Subject: {subject}\n\n"
        f"{body[:3000]}"
    )

    result = agent.run_sync(prompt)
    return result.output


# ---------------------------------------------------------------------------
# Main triage pipeline
# ---------------------------------------------------------------------------


def _record_inbound_message(
    thread_id: int,
    gmail_message_id: str,
    subject: str,
    body: str,
) -> None:
    with SessionLocal() as session:
        existing = (
            session.query(Message).filter_by(gmail_message_id=gmail_message_id).first()
        )
        if existing:
            return
        session.add(
            Message(
                thread_id=thread_id,
                gmail_message_id=gmail_message_id,
                direction="inbound",
                subject=subject,
                body=body,
            )
        )
        thread = session.get(SupplierThread, thread_id)
        if thread and thread.state == "OUTREACH_SENT":
            thread.state = "AWAITING_REPLY"
        session.commit()


def _link_gmail_thread(thread_id: int, gmail_thread_id: str) -> None:
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if thread and not thread.gmail_thread_id:
            thread.gmail_thread_id = gmail_thread_id
            session.commit()


def _get_known_gmail_threads() -> dict[str, int]:
    """Return {gmail_thread_id: supplier_thread_id} for all linked threads."""
    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(SupplierThread.gmail_thread_id.isnot(None))
            .all()
        )
        return {t.gmail_thread_id: t.id for t in threads}


def triage_inbox() -> dict:
    """Poll Gmail inbox and triage all unread threads.

    Returns a summary dict with counts per action taken.
    """
    gmail = GmailService()
    ignore_emails = _get_ignore_emails()
    known_threads = _get_known_gmail_threads()
    unread = gmail.list_unread_threads()

    counts = {
        "archived_noise": 0,
        "flagged_notification": 0,
        "supplier_reply": 0,
        "flagged": 0,
        "errors": 0,
    }

    for thread_stub in unread:
        gmail_thread_id = thread_stub["id"]
        try:
            thread_data = gmail.get_thread(gmail_thread_id)
            messages = thread_data.get("messages", [])
            if not messages:
                continue

            latest = messages[-1]
            sender_name, sender_email = _extract_sender(latest)
            subject = _extract_subject(latest)
            body = _extract_body(latest)
            msg_id = latest.get("id", "")

            # Tier 0: known supplier thread — skip LLM, record directly
            if gmail_thread_id in known_threads:
                thread_id = known_threads[gmail_thread_id]
                _record_inbound_message(thread_id, msg_id, subject, body)
                gmail.archive_thread(gmail_thread_id)
                counts["supplier_reply"] += 1
                log.info(
                    "Known thread %d — recorded reply and archived: %s",
                    thread_id,
                    subject[:60],
                )
                continue

            # Tier 1: auto-archive known noise
            if sender_email in ignore_emails:
                gmail.archive_thread(gmail_thread_id)
                log.info("Auto-archived noise from %s: %s", sender_email, subject[:60])
                counts["archived_noise"] += 1
                continue

            # Tier 2: no-reply senders — archive. Platform notification
            # emails are now handled by _poll_platform_messages() below.
            if _is_no_reply_sender(sender_name, sender_email):
                if _is_platform_notification(subject, body):
                    counts["flagged_notification"] += 1
                    log.info(
                        "Archived platform notification from %s: %s "
                        "(platform polling handles the actual message)",
                        sender_email,
                        subject[:60],
                    )
                gmail.archive_thread(gmail_thread_id)
                counts["archived_noise"] += 1
                continue

            # Tier 3: LLM triage
            result = _triage_with_llm(sender_name, sender_email, subject, body)
            log.info(
                "LLM triage for %s <%s>: %s (thread=%s) — %s",
                sender_name,
                sender_email,
                result.action,
                result.thread_id,
                result.reason[:80],
            )

            if result.action == "add_to_ignore_list":
                _add_ignore_email(sender_email)
                gmail.archive_thread(gmail_thread_id)
                counts["archived_noise"] += 1
                log.info(
                    "Added %s to ignore list and archived",
                    sender_email,
                )

            elif result.action == "archive":
                gmail.archive_thread(gmail_thread_id)
                counts["archived_noise"] += 1

            elif result.action == "reply_supplier" and result.thread_id:
                _record_inbound_message(result.thread_id, msg_id, subject, body)
                _link_gmail_thread(result.thread_id, gmail_thread_id)
                gmail.archive_thread(gmail_thread_id)
                counts["supplier_reply"] += 1
                log.info(
                    "Supplier reply recorded — thread %d: %s",
                    result.thread_id,
                    result.summary[:100],
                )

            elif result.action == "flag_human":
                counts["flagged"] += 1
                log.warning(
                    "Flagged for human review: %s <%s> — %s",
                    sender_name,
                    sender_email,
                    result.reason,
                )
                _alert_maintainer(
                    gmail,
                    sender_email,
                    subject,
                    body,
                    tag="Flagged",
                    preamble=f"Flagged for human review: {result.reason}",
                )

            else:
                counts["flagged"] += 1
                log.warning("Unexpected triage result: %s", result)

        except Exception:
            log.exception("Error triaging gmail thread %s", gmail_thread_id)
            counts["errors"] += 1

    log.info(
        "Gmail triage: %d archived (noise), %d platform notifications, "
        "%d supplier replies, %d flagged, %d errors",
        counts["archived_noise"],
        counts["flagged_notification"],
        counts["supplier_reply"],
        counts["flagged"],
        counts["errors"],
    )

    # Platform message polling
    platform_count = _poll_platform_messages()
    counts["platform_messages"] = platform_count

    log.info("Inbox triage complete: %s", counts)
    return counts


# ---------------------------------------------------------------------------
# Platform message polling
# ---------------------------------------------------------------------------


def _get_platforms_with_active_threads() -> set[str]:
    """Return platform names that have threads awaiting replies."""
    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(
                SupplierThread.state.in_(
                    ["OUTREACH_SENT", "AWAITING_REPLY", "NEGOTIATING"]
                )
            )
            .all()
        )
        return {t.supplier_product.platform for t in threads}


def _match_supplier_thread(
    supplier_name: str,
    platform_name: str,
    product_url: str | None = None,
) -> SupplierThread | None:
    """Match a platform message to an active supplier thread.

    When product_url is available (extracted from the inquiry card in the
    conversation), matches via supplier_products.product_url for precision —
    handles the case where one supplier has multiple threads for different
    products. Falls back to supplier name matching when product_url is absent.
    """
    from app.db.models.supplier_product import SupplierProduct

    active_states = ["OUTREACH_SENT", "AWAITING_REPLY", "NEGOTIATING"]

    with SessionLocal() as session:
        if product_url:
            thread = (
                session.query(SupplierThread)
                .join(
                    SupplierProduct,
                    SupplierThread.supplier_product_id == SupplierProduct.id,
                )
                .filter(
                    SupplierProduct.product_url == product_url,
                    SupplierThread.state.in_(active_states),
                )
                .first()
            )
            if thread:
                log.info(
                    "Matched thread %d via product_url: %s", thread.id, product_url
                )
                session.expunge(thread)
                return thread
            log.warning(
                "No thread matched product_url %s — falling back to name", product_url
            )

        supplier = (
            session.query(Supplier)
            .filter(
                Supplier.name == supplier_name,
                Supplier.platform == platform_name,
            )
            .first()
        )

        if not supplier:
            supplier = (
                session.query(Supplier)
                .filter(
                    Supplier.name.ilike(f"%{supplier_name}%"),
                    Supplier.platform == platform_name,
                )
                .first()
            )

        if not supplier:
            suppliers = (
                session.query(Supplier).filter(Supplier.platform == platform_name).all()
            )
            for s in suppliers:
                if s.name.lower() in supplier_name.lower():
                    supplier = s
                    break

        if not supplier:
            return None

        thread = (
            session.query(SupplierThread)
            .filter(
                SupplierThread.supplier_id == supplier.id,
                SupplierThread.state.in_(active_states),
            )
            .order_by(SupplierThread.last_updated.desc())
            .first()
        )
        if thread:
            session.expunge(thread)
        return thread


def _record_platform_inbound(
    thread_id: int,
    message_text: str,
    conversation_url: str,
    sent_at: str | None = None,
) -> bool:
    """Record an inbound platform message and update thread state.

    Returns True if a new message was recorded, False if it was a duplicate.
    Deduplicates by checking for an existing message with the same body text
    on the same thread — platform messages have no unique ID like Gmail does.
    """
    from datetime import datetime, timezone

    with SessionLocal() as session:
        existing = (
            session.query(Message)
            .filter_by(thread_id=thread_id, direction="inbound", body=message_text)
            .first()
        )
        if existing:
            return False

        msg = Message(
            thread_id=thread_id,
            direction="inbound",
            channel="platform",
            body=message_text,
        )
        if sent_at:
            msg.sent_at = datetime.fromisoformat(sent_at).replace(tzinfo=timezone.utc)
        session.add(msg)
        thread = session.get(SupplierThread, thread_id)
        if thread:
            if thread.state == "OUTREACH_SENT":
                thread.state = "AWAITING_REPLY"
            thread.channel = "platform"
            if not thread.platform_thread_url:
                thread.platform_thread_url = conversation_url
        session.commit()
        return True


class InboxPollExecutor(BrowserFallbackExecutor):
    """Concurrent platform inbox polling with agent fallback."""

    @property
    def stage(self) -> str:
        return "s4_inbox"

    @property
    def action(self) -> str:
        return "read_messages"

    def get_work_items(self) -> dict[str, list[str]]:
        platforms_needed = _get_platforms_with_active_threads()
        return {name: [name] for name in platforms_needed}

    def deterministic_action(self, item: str, page, platform, context_id: str):
        return platform.read_platform_messages(page)

    def agent_fallback(self, item: str, session_id: str, platform) -> FallbackResult:
        from app.pipeline.agents.platform_message_agent import (
            InboxStatus,
            read_inbox_via_agent,
        )
        from app.services.platforms.platform import PlatformMessage

        result = read_inbox_via_agent(
            session_id,
            platform_prompt=platform.messaging_agent_prompt,
        )
        if result.status == InboxStatus.SUCCESS:
            messages = [
                PlatformMessage(
                    supplier_name=m.supplier_name,
                    message_text=m.message_text,
                    conversation_url=m.conversation_url,
                )
                for m in result.messages
            ]
            return FallbackResult(success=True, result=messages)
        return FallbackResult(
            success=False,
            login_required=(result.status == InboxStatus.LOGIN_REQUIRED),
        )

    def on_success(self, item: str, result) -> None:
        platform_name = item
        for msg in result:
            thread = _match_supplier_thread(
                msg.supplier_name, platform_name, msg.product_url
            )
            if not thread:
                log.warning(
                    "Could not match platform message from '%s' to any thread",
                    msg.supplier_name,
                )
                continue
            is_new = _record_platform_inbound(
                thread.id, msg.message_text, msg.conversation_url, msg.sent_at
            )
            if is_new:
                log.info(
                    "Recorded platform message from '%s' → thread %d",
                    msg.supplier_name,
                    thread.id,
                )
            else:
                log.debug(
                    "Skipped duplicate platform message from '%s' on thread %d",
                    msg.supplier_name,
                    thread.id,
                )

    def thread_label(self, item: str) -> str:
        return f"poll-{item}"


def _poll_platform_messages() -> int:
    """Poll platform messaging for all platforms with active threads.

    Deterministic Playwright read first, agent fallback on failure.
    Returns count of new messages recorded.
    """
    platforms_needed = _get_platforms_with_active_threads()
    if not platforms_needed:
        log.info("No platforms with active threads — skipping platform polling")
        return 0

    results = InboxPollExecutor().execute()
    total = sum(len(msgs) for _, msgs in results if msgs)
    log.info("Platform polling complete: %d messages recorded", total)
    return total
