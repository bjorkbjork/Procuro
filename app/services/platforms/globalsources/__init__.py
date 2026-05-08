"""GlobalSources supplier platform.

Search, login, inquiry, spec parsing — all deterministic Playwright scripts.
Chat messaging uses deterministic scripts with LLM agent fallback via
BrowserFallbackExecutor when selectors don't match."""

from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.globalsources.messaging import (
    read_platform_messages as _read_messages,
    send_platform_reply as _send_reply,
)
from app.services.platforms.globalsources.service import (
    login_globalsources,
    parse_product_specs,
    parse_product_title,
    search_suppliers,
    send_product_inquiry,
)
from app.services.platforms.platform import PlatformMessage


class Platform:
    platform = PlatformEnum.GLOBALSOURCES

    # Verified: GS specs live in Ant Design description tables inside #Product
    spec_selector = "#Product .ant-descriptions"

    inquiry_agent_prompt = """\
## GlobalSources-specific guidance

GlobalSources product pages use standard DOM elements — NO iframes. CSS \
selectors work directly (unlike Alibaba where you need snapshot refs for \
iframe elements).

The inquiry form is **inline at the bottom of the product page** (not a \
modal or popup). Scroll down to find the section titled "Send a direct \
inquiry to this supplier".

### CSS selectors (verified)

- Message textarea: `textarea.msg-input`
- Email input: `.email-box input.ant-select-search__field`
- Submit button: `button.send-btn` (text: "Send Inquiry Now")

### Step-by-step

1. **Navigate to the product page** if not already there. Wait for the page \
   to fully load.
2. **Scroll to the inquiry form**: Scroll down to find the inline inquiry \
   form. You can also click the "Inquire Now" button near the top of the \
   page — it scrolls to the form section.
3. **Fill the message**: Use `fill textarea.msg-input <message>` to fill \
   the message textarea. Use `fill`, NOT `type`.
4. **Fill the email**: This is REQUIRED. The email field is below the \
   message textarea. Use `fill ".email-box input.ant-select-search__field" \
   <email>` — extract the email address from the inquiry message you were \
   given (it appears after "please contact us directly via email at"). \
   The form WILL NOT submit without an email address.
5. **Submit**: Click `button.send-btn` (the red "Send Inquiry Now" button).
6. **Verify submission**: Look for a success message or the form state \
   changing after submit.

### Critical rules

- **NEVER navigate away from the product page.** The form is inline — \
  everything happens on the same page. If you navigate to a different URL, \
  you will lose the form state and may lose your login session.
- If the page shows "Sign in / Register" in the header, that is normal — \
  the inline inquiry form still works without full login. Fill in the \
  email field and submit.
- Do NOT click any navigation links, tabs, or buttons that would leave \
  the product page.
- If you see a CAPTCHA after clicking submit, try to solve it. If you \
  cannot solve it after 2 attempts, call finish with FAILED."""

    messaging_agent_prompt = """\
## GlobalSources chat guidance

The GS chat is a separate SPA at chat.globalsources.com — NOT on the main \
GS website.

### Chat URL (verified)

Navigate to: https://chat.globalsources.com/buyer?p=eyJwbGF0Zm9ybUNvZGUiOiJOR1MtRCIsImxhbmciOiJlbnVzIn0=

### Layout

- Left sidebar: Chats / Contacts icons
- Conversation list (280px panel): "All" / "Unread" tabs, then conversation \
  items showing contact name, company, time, and preview
- Chat panel (center): message history with input at the bottom
- Right panel: supplier company details

### CSS selectors (verified)

- Conversation tabs: `.tool-tabs > div`
- Conversation items: `.cursor-pointer .font-700` (contact name)
- Message area: `.msg-content`
- Message textarea: `.input-msg-text-area textarea` \
  (placeholder: "Please type your message here...")
- Send button: `.send-btn` (disabled when textarea is empty)

### Reading messages

- Click "All" or "Unread" tab to filter conversations.
- Click a conversation to open it. Contact name is in the header.
- Our messages appear on the right, supplier messages on the left.
- Messages may include inquiry cards with product links.

### Sending replies

1. Click the conversation you want to reply to.
2. Fill the textarea at the bottom of the chat panel using `fill`.
3. The Send button becomes active once text is entered.
4. Click the Send button or press Enter to send.
5. Verify the message appears in the conversation.

### Critical rules

- **NEVER navigate away from chat.globalsources.com.** Everything happens \
  within the chat SPA.
- If you see a login page or redirect away from chat.globalsources.com, \
  call finish with LOGIN_REQUIRED. Do NOT attempt to log in."""

    def search(self, query: str, page_size: int = 20) -> list[dict]:
        return search_suppliers(query, page_size=page_size)

    def parse_specs(self, html: str) -> dict:
        return parse_product_specs(html)

    def parse_title(self, html: str) -> str:
        return parse_product_title(html)

    def login(self, page: Page, session_url: str = "") -> None:
        login_globalsources(page, session_url=session_url)

    def send_inquiry(self, page: Page, product_url: str, message: str) -> bool:
        return send_product_inquiry(page, product_url, message)

    def url_slug(self, product_url: str) -> str:
        # Verified: /OLED-TV/55-inch-smart-tv-1212888159p.htm
        return product_url.rstrip("/").split("/")[-1].replace(".htm", "")

    def read_platform_messages(self, page: Page) -> list[PlatformMessage]:
        results = _read_messages(page)
        return [
            PlatformMessage(
                supplier_name=r["supplier_name"],
                message_text=r["message_text"],
                conversation_url=r["conversation_url"],
                product_url=r.get("product_url"),
                sent_at=r.get("sent_at"),
            )
            for r in results
        ]

    def send_platform_reply(
        self, page: Page, conversation_url: str, message: str
    ) -> bool:
        return _send_reply(page, conversation_url, message)
