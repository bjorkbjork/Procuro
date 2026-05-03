"""Stage 4: Monitor Gmail inbox and triage supplier replies.

Three-tier filter:
1. Auto-archive known noise (AWS, Google, Alibaba notifications, etc.)
2. No-reply senders not in the ignore list get scanned for platform message
   notifications (Alibaba/GlobalSources have their own messaging systems)
3. Everything else goes to the LLM triage agent for classification

The ignore-email list is stored in the keyvalue table and the LLM agent can
add new addresses to it via a tool."""

import email.utils
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.base.config import model_settings, settings
from app.base.llm import Agent, get_model
from app.db.database import SessionLocal
from app.db.models.keyvalue import KeyValue
from app.db.models.message import Message
from app.db.models.supplier_thread import SupplierThread
from app.services.gmail import GmailService

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


# ---------------------------------------------------------------------------
# LLM triage agent
# ---------------------------------------------------------------------------


class TriageResult(BaseModel):
    action: str = Field(
        description="One of: 'reply_supplier', 'archive', 'flag_human'",
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

2. **archive** — This is noise: marketing, newsletters, automated platform \
   notifications, or irrelevant messages. If the sender should be permanently \
   ignored, use the add_ignore_email tool BEFORE returning your result.

3. **flag_human** — You're unsure what this is, or it requires human attention \
   (e.g. legal request, account issue, something unexpected).

Rules:
- If you can match the email to a specific supplier thread (by company name, \
  product, or prior conversation), set thread_id.
- If a supplier writes in a non-English language, still classify it as \
  reply_supplier — the negotiation agent will handle the language barrier.
- Platform notifications about new messages should be archived (the actual \
  supplier message comes via email separately).
- When in doubt, flag_human rather than archiving a real supplier reply."""


def _make_triage_tools() -> list[Tool]:
    def add_ignore_email(address: str) -> str:
        """Add an email address to the permanent ignore list. Use this for
        senders that should always be auto-archived (newsletters, notifications,
        marketing). Do NOT add actual supplier email addresses."""
        return _add_ignore_email(address)

    return [Tool(add_ignore_email, takes_ctx=False)]


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
        system_prompt=TRIAGE_SYSTEM_PROMPT,
        output_type=TriageResult,
        tools=_make_triage_tools(),
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
        "archived_notification": 0,
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
                    thread_id, subject[:60],
                )
                continue

            # Tier 1: auto-archive known noise
            if sender_email in ignore_emails:
                gmail.archive_thread(gmail_thread_id)
                log.info("Auto-archived noise from %s: %s", sender_email, subject[:60])
                counts["archived_noise"] += 1
                continue

            # Tier 2: no-reply senders — check for platform notifications
            if _is_no_reply_sender(sender_name, sender_email):
                if _is_platform_notification(subject, body):
                    gmail.archive_thread(gmail_thread_id)
                    log.info(
                        "Archived platform notification from %s: %s",
                        sender_email,
                        subject[:60],
                    )
                    counts["archived_notification"] += 1
                    continue
                # Not a recognized notification — fall through to LLM

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

            if result.action == "archive":
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

            else:
                counts["flagged"] += 1
                log.warning("Unexpected triage result: %s", result)

        except Exception:
            log.exception("Error triaging gmail thread %s", gmail_thread_id)
            counts["errors"] += 1

    log.info(
        "Inbox triage complete: %d archived (noise), %d archived (notifications), "
        "%d supplier replies, %d flagged, %d errors",
        counts["archived_noise"],
        counts["archived_notification"],
        counts["supplier_reply"],
        counts["flagged"],
        counts["errors"],
    )
    return counts
