from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.alibaba.service import (
    login_alibaba,
    parse_product_specs,
    parse_product_title,
    search_suppliers,
    send_product_inquiry,
)
from app.services.platforms.alibaba.messaging import (
    read_platform_messages as _read_messages,
    send_platform_reply as _send_reply,
)
from app.services.platforms.platform import PlatformMessage


class Platform:
    platform = PlatformEnum.ALIBABA
    spec_selector = "[data-testid='module-attribute']"
    inquiry_agent_prompt = """\
## Alibaba-specific guidance

The inquiry form opens inside an iframe (message.alibaba.com). CSS selectors \
cannot reach into the iframe — you MUST use snapshot refs (`@3-NNN`) for \
any element inside it.

### Step-by-step

1. **Open the form**: `click [data-testid='customizationSkuSummary-INQUIRY']` \
   (this is on the main page, CSS works).
2. **Find iframe elements**: Run `snapshot` — iframe elements have refs \
   prefixed with `@3-`. Look for the textarea (class `content-input`) and \
   the submit button (class `next-btn-primary`, text "Send Inquiry").
3. **Fill the message**: `fill @3-<textarea-ref> <message>`
4. **Submit**: `click @3-<submit-ref>` — this is the iframe's Send button, \
   NOT the one on the product page.

IMPORTANT: There are TWO "Send Inquiry" buttons. The page-level one opens \
the form. The iframe one (`@3-...`) submits it. After filling, always click \
the iframe submit button.

**Captcha**: If a text captcha exists, it is visible right next to the iframe \
Send button as blue distorted characters with an input field. If you don't \
see it in your screenshot, there is no captcha — just click Submit.

**Textarea**: May contain placeholder text. Use `fill` to replace it, NOT `type`.

**Confirmation**: Look for "Your inquiry has been sent" or the form disappearing."""

    messaging_agent_prompt = """\
## Alibaba message center guidance

The messaging interface is at https://message.alibaba.com/. After login it \
shows a conversation list on the left and the active conversation on the right.

### Reading messages
- Unread conversations are usually indicated by bold text or a dot/badge.
- Click a conversation to open it. The supplier name is in the header.
- Messages appear as chat bubbles — the latest is at the bottom.
- Use `get url` after opening a conversation to capture its URL.

### Sending replies
- The message input is at the bottom of the conversation panel.
- It may be a textarea or a contenteditable div.
- After typing, click the Send button (usually blue, bottom-right).
- Verify the message appears in the conversation before reporting SENT."""

    def search(self, query: str, page_size: int = 20) -> list[dict]:
        return search_suppliers(query, page_size=page_size)

    def parse_specs(self, html: str) -> dict:
        return parse_product_specs(html)

    def parse_title(self, html: str) -> str:
        return parse_product_title(html)

    def login(self, page: Page, session_url: str = "") -> None:
        login_alibaba(page, session_url=session_url)

    def send_inquiry(self, page: Page, product_url: str, message: str) -> bool:
        return send_product_inquiry(page, product_url, message)

    def url_slug(self, product_url: str) -> str:
        return product_url.split("/")[-1].split("?")[0]

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
