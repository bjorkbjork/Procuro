from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.alibaba.service import (
    login_alibaba,
    parse_product_specs,
    parse_product_title,
    search_suppliers,
    send_product_inquiry,
)


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
