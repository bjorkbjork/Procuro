"""Deterministic Playwright automation for GlobalSources chat.

Primary path for reading supplier messages and sending replies. Falls back
to the LLM agent in platform_message_agent.py when selectors don't match
(same pattern as service.py + inquiry_agent for inquiry submission).

Selectors derived from the rendered DOM of the live chat SPA
(2026-05-08). The DOM is a Vue/Element Plus SPA at chat.globalsources.com."""

import logging
from pathlib import Path
from urllib.parse import quote, unquote

import stamina
from playwright.sync_api import Error as PlaywrightError, Page

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_JS_EXTRACT_CONVERSATIONS = (_DIR / "extract_conversations.js").read_text()
_JS_EXTRACT_CHAT_MESSAGES = (_DIR / "extract_chat_messages.js").read_text()
_JS_EXTRACT_SUPPLIER_NAME = (_DIR / "extract_supplier_name.js").read_text()
_JS_EXTRACT_INQUIRY_PRODUCT = (_DIR / "extract_inquiry_product.js").read_text()

# Verified from html_test_fixtures/gs_chat_rendered.html
# Base64 payload: {"platformCode":"NGS-D","lang":"enus"}
CHAT_URL = (
    "https://chat.globalsources.com/buyer"
    "?p=eyJwbGF0Zm9ybUNvZGUiOiJOR1MtRCIsImxhbmciOiJlbnVzIn0="
)

# Verified from html_test_fixtures/gs_chat_rendered.html
CHAT_LOADED_SELECTOR = ".tool-tabs"
MESSAGE_INPUT = ".input-msg-text-area textarea"
SEND_BUTTON = ".send-btn"
MSG_CONTENT = ".msg-content"

MAX_CONVERSATIONS = 20


class PlatformLoginRequired(Exception):
    """Session cookies expired — needs re-authentication."""


def _check_login(page: Page) -> None:
    """Raise PlatformLoginRequired if the page shows a login form."""
    if "login" in page.url and "chat.globalsources.com" not in page.url:
        raise PlatformLoginRequired("Redirected to login page")


@stamina.retry(on=PlaywrightError, attempts=5, timeout=120)
def _navigate_to_chat(page: Page) -> None:
    """Navigate to the GS chat and wait for the conversation list to render."""
    page.goto(CHAT_URL, wait_until="domcontentloaded")
    _check_login(page)
    page.wait_for_selector(CHAT_LOADED_SELECTOR, timeout=30_000)
    log.info("Chat loaded at %s", page.url)


def _click_tab(page: Page, tab_text: str) -> None:
    """Click All or Unread tab in the conversation list."""
    tabs = page.locator(".tool-tabs > div")
    for i in range(tabs.count()):
        if tab_text.lower() in tabs.nth(i).text_content().lower():
            tabs.nth(i).click()
            page.wait_for_timeout(2_000)
            return
    log.warning("Tab '%s' not found", tab_text)


def _click_conversation(page: Page, name: str) -> None:
    """Click a conversation in the sidebar by contact name."""
    # .cursor-pointer .font-700 matches contact names inside conversation
    # items but not tab labels (which have both classes on the same element)
    page.locator(".cursor-pointer .font-700", has_text=name).first.click()
    page.wait_for_selector(MSG_CONTENT, timeout=30_000)
    # Wait for actual message content to render inside the chat area
    # (the container appears before the SPA populates the messages)
    page.wait_for_selector(f"{MSG_CONTENT} .break-words", timeout=10_000)
    page.wait_for_timeout(2_000)


def read_platform_messages(page: Page, *, unread_only: bool = True) -> list[dict]:
    """Read conversations from the GlobalSources chat.

    When unread_only=True (production default), clicks the Unread tab and
    processes conversations one at a time. Opening a conversation marks it
    as read, so the next iteration picks up the next unread.

    Returns a list of dicts with keys: supplier_name, message_text,
    conversation_url, product_url, sent_at.
    Raises PlatformLoginRequired if auth cookies have expired.
    """
    _navigate_to_chat(page)

    all_messages: list[dict] = []
    processed = 0

    _click_tab(page, "Unread" if unread_only else "All")
    conversations = page.evaluate(_JS_EXTRACT_CONVERSATIONS)

    if not conversations:
        log.info("No conversations found")
        return all_messages

    log.info("Found %d conversation(s)", len(conversations))

    while processed < min(len(conversations), MAX_CONVERSATIONS):
        conv = conversations[0] if unread_only else conversations[processed]
        target_name = conv["name"]

        _click_conversation(page, target_name)
        _check_login(page)

        supplier_info = page.evaluate(_JS_EXTRACT_SUPPLIER_NAME)
        supplier_name = supplier_info.get("company") or target_name
        contact_name = supplier_info.get("name") or target_name

        # GS chat is a SPA — the URL may not include a conversation ID.
        # Encode the contact name into the URL fragment so send_platform_reply
        # can find and re-select this conversation later.
        base_url = page.url.split("#")[0]
        conversation_url = f"{base_url}#contact={quote(contact_name)}"

        product_url = page.evaluate(_JS_EXTRACT_INQUIRY_PRODUCT)
        messages = page.evaluate(_JS_EXTRACT_CHAT_MESSAGES)

        log.info(
            "Conversation with %s (%s): %d supplier messages, product_url=%s",
            supplier_name,
            contact_name,
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

        if unread_only:
            _click_tab(page, "Unread")
            conversations = page.evaluate(_JS_EXTRACT_CONVERSATIONS)
            if not conversations:
                break

    log.info(
        "Chat read complete: %d conversations, %d messages",
        processed,
        len(all_messages),
    )
    return all_messages


def send_platform_reply(page: Page, conversation_url: str, message: str) -> bool:
    """Send a reply in a GlobalSources chat conversation.

    The conversation_url may contain a #contact=<name> fragment (encoded by
    read_platform_messages) to identify which conversation to open.

    Returns True if the message was sent successfully.
    Raises PlatformLoginRequired if auth cookies have expired.
    """
    contact_name = None
    base_url = conversation_url
    if "#contact=" in conversation_url:
        base_url = conversation_url.split("#")[0]
        contact_name = unquote(conversation_url.split("#contact=")[1])

    page.goto(base_url, wait_until="domcontentloaded")
    _check_login(page)
    page.wait_for_selector(CHAT_LOADED_SELECTOR, timeout=30_000)

    if contact_name:
        _click_conversation(page, contact_name)
    elif page.locator(MESSAGE_INPUT).count() == 0:
        log.warning("No conversation active and no contact name to select")
        return False

    page.wait_for_selector(MESSAGE_INPUT, timeout=30_000)

    input_el = page.locator(MESSAGE_INPUT).first
    input_el.fill(message)
    page.wait_for_timeout(500)
    log.info("Reply filled at %s", conversation_url)

    send_btn = page.locator(SEND_BUTTON).first
    if send_btn.is_disabled():
        log.warning("Send button still disabled after filling message")
        return False

    send_btn.click()
    page.wait_for_timeout(2_000)

    page_text = page.inner_text("body")
    if message in page_text:
        log.info("Reply confirmed at %s", conversation_url)
        return True

    log.warning("Reply not confirmed at %s", conversation_url)
    return False
