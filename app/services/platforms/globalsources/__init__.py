"""GlobalSources supplier platform — agent-first implementation.

Deterministic browser methods (inquiry, messaging) raise NotImplementedError
to trigger the BrowserFallbackExecutor's LLM agent path. The agent prompts
below guide the agent through GS's UI. Once agent behavior is observed via
Browserbase recordings, the deterministic Playwright scripts will be written
to replace the stubs."""

from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum
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
## GlobalSources message center guidance

### Finding the message center
- Navigate to the GlobalSources message center. Try these paths:
  - Look for "Messages", "My Messages", or an envelope/chat icon in the \
    top navigation bar.
  - Try navigating directly to https://www.globalsources.com/messages/ \
    or https://www.globalsources.com/user/messages/
  - If neither works, look in "My Account" or "My GlobalSources" dropdown \
    for a messaging link.

### Reading messages
- Unread conversations are typically indicated by bold text, a badge/dot, \
  or different background color.
- Click a conversation to open it. The supplier/company name should be \
  visible in the conversation header or sidebar.
- Messages appear in chronological order — the latest is usually at the \
  bottom.
- Use `get url` after opening a conversation to capture its URL.

### Sending replies
- The message input is at the bottom of the conversation panel — look for \
  a textarea, input field, or contenteditable div.
- After typing the message, click the Send button (usually a blue/green \
  button near the input area, or a paper plane icon).
- Verify the message appears in the conversation before reporting SENT.

### If login is required
- If you see a login page, registration form, or "Sign in" prompt, call \
  finish immediately with LOGIN_REQUIRED. Do NOT attempt to log in."""

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
        raise NotImplementedError(
            "GS message reading not yet scripted — agent fallback will handle"
        )

    def send_platform_reply(
        self, page: Page, conversation_url: str, message: str
    ) -> bool:
        raise NotImplementedError(
            "GS reply sending not yet scripted — agent fallback will handle"
        )
