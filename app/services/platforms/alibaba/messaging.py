"""Deterministic Playwright automation for Alibaba's message center.

Primary path for reading supplier messages and sending replies. Falls back
to the LLM agent in platform_message_agent.py when selectors don't match
(same pattern as service.py + inquiry_agent for inquiry submission).

Selectors derived from the accessibility snapshot of the live message center
(2026-05-04). The DOM is a React SPA at message.alibaba.com."""

import logging
from pathlib import Path

import stamina
from playwright.sync_api import Page, Error as PlaywrightError

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_JS_EXTRACT_CONVERSATIONS = (_DIR / "extract_conversations.js").read_text()
_JS_EXTRACT_SUPPLIER_MESSAGES = (_DIR / "extract_supplier_messages.js").read_text()
_JS_EXTRACT_SUPPLIER_NAME = (_DIR / "extract_supplier_name.js").read_text()
_JS_EXTRACT_INQUIRY_PRODUCT = (_DIR / "extract_inquiry_product.js").read_text()

MESSAGE_CENTER_URL = "https://message.alibaba.com/"

UNREAD_TAB = "text=Unread"
ALL_TAB = "text=All"
MESSAGE_INPUT = ".send-textarea"
SEND_BUTTON = ".send-tool-button"

MAX_CONVERSATIONS = 20


class PlatformLoginRequired(Exception):
    """Session cookies expired — needs re-authentication."""


def _check_login(page: Page) -> None:
    """Raise PlatformLoginRequired if the page shows a login form."""
    if "login.alibaba.com" in page.url:
        raise PlatformLoginRequired("Redirected to login page")
    if page.locator("text=Sign in").count() > 0:
        raise PlatformLoginRequired("Login form detected on page")
    if page.locator("text=Continue with Google").count() > 0:
        raise PlatformLoginRequired("Login form detected on page")


def _navigate_to_conversation(page: Page, url: str) -> None:
    """Navigate to a specific conversation and wait for the chat to render."""
    log.info("Navigating to conversation: %s", url)
    page.goto(url, wait_until="domcontentloaded")
    _check_login(page)
    log.info("Page loaded, waiting for textarea...")
    page.wait_for_selector(MESSAGE_INPUT, timeout=60_000)
    log.info("Conversation loaded at %s", url)


@stamina.retry(on=PlaywrightError, attempts=5, timeout=120)
def _navigate_to_inbox(page: Page) -> None:
    """Navigate to the message center and wait for the inbox to render.

    Alibaba's SPA is slow — the initial load can take 30s+. Stamina retries
    with backoff so we survive slow renders without giving up too early.
    """
    page.goto(MESSAGE_CENTER_URL, wait_until="commit")
    _check_login(page)
    page.wait_for_selector("text=Inbox", timeout=30_000)
    log.info("Inbox loaded at %s", page.url)


def read_platform_messages(page: Page, *, unread_only: bool = True) -> list[dict]:
    """Read conversations from the Alibaba message center.

    When unread_only=True (production default), clicks the Unread filter and
    processes one conversation at a time. Opening a conversation marks it as
    read, so the next iteration picks up the next unread. Loops until no
    unread conversations remain.

    When unread_only=False (for testing), reads all conversations instead.

    Returns a list of dicts with keys: supplier_name, message_text,
    conversation_url. Each supplier message is a separate entry.
    Raises PlatformLoginRequired if auth cookies have expired.
    """
    _navigate_to_inbox(page)

    all_messages: list[dict] = []
    processed = 0
    filter_tab = UNREAD_TAB if unread_only else ALL_TAB

    while processed < MAX_CONVERSATIONS:
        page.click(filter_tab)
        page.wait_for_timeout(3_000)

        names = page.evaluate(_JS_EXTRACT_CONVERSATIONS)

        if not names:
            log.info("No conversations found")
            break

        if processed >= len(names):
            log.info("All conversations processed")
            break

        log.info("Found %d conversation(s): %s", len(names), names)

        target_name = names[0] if unread_only else names[processed]
        page.click(f"text={target_name}")
        page.wait_for_selector(".message-item-wrapper", timeout=30_000)
        _check_login(page)

        supplier_info = page.evaluate(_JS_EXTRACT_SUPPLIER_NAME)
        supplier_name = supplier_info.get("company") or target_name

        conversation_url = page.url
        product_url = page.evaluate(_JS_EXTRACT_INQUIRY_PRODUCT)
        messages = page.evaluate(_JS_EXTRACT_SUPPLIER_MESSAGES)

        log.info(
            "Conversation with %s: %d messages, product_url=%s",
            supplier_name,
            len(messages),
            product_url,
        )

        for msg in messages:
            all_messages.append(
                {
                    "supplier_name": supplier_name,
                    "message_text": msg["text"],
                    "conversation_url": conversation_url,
                    "product_url": product_url,
                    "sent_at": msg.get("sent_at"),
                }
            )
            log.info("Read message from %s: %s", supplier_name, msg["text"])

        processed += 1

    log.info(
        "Platform read complete: %d conversations, %d messages",
        processed,
        len(all_messages),
    )
    return all_messages


def send_platform_reply(page: Page, conversation_url: str, message: str) -> bool:
    """Send a reply in an Alibaba message center conversation.

    Returns True if the message was sent successfully.
    Raises PlatformLoginRequired if auth cookies have expired.
    """
    _navigate_to_conversation(page, conversation_url)

    input_el = page.locator(MESSAGE_INPUT).first
    input_el.fill(message)
    page.wait_for_timeout(500)
    log.info("Reply filled at %s", conversation_url)

    send_btn = page.locator(SEND_BUTTON).first
    if send_btn.count() == 0:
        log.warning("Send button not found at %s", conversation_url)
        return False

    send_btn.click()
    page.wait_for_timeout(2_000)

    page_text = page.inner_text("body")
    if message in page_text:
        log.info("Reply confirmed at %s", conversation_url)
        return True

    log.warning("Reply click fired but not confirmed at %s", conversation_url)
    return False
