"""GlobalSources supplier search, product page parsing, and login.

Search uses their internal JSON API behind Incapsula bot protection, so
requests are routed through a Browserbase session. Parsing functions extract
specs from product page HTML — the caller fetches HTML via Browserbase.
Login uses Google OAuth (same shared Gmail credentials as Alibaba)."""

import logging
import re

import stamina
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError, Page

from app.base.config import settings
from app.services.browser import BrowserSession
from app.services.google_auth import google_login

log = logging.getLogger(__name__)

SEARCH_PATH = "/api/agg-search/DESKTOP/v3/product/search"
LANDING_URL = "https://www.globalsources.com"

_JS_SEARCH = """\
async (payload) => {
    const resp = await fetch('%s', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'lang': 'enus'},
        body: JSON.stringify(payload)
    });
    return resp.json();
}
""" % SEARCH_PATH


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def search_suppliers(
    query: str,
    page: int = 1,
    page_size: int = 20,
) -> list[dict]:
    """Search GlobalSources for suppliers matching a product query.

    Opens a Browserbase session, navigates to GS to pass bot protection,
    then calls the search API via in-page fetch.
    """
    payload = {
        "pageNum": page,
        "pageSize": page_size,
        "query": query,
        "popupFlag": False,
    }

    with BrowserSession(proxy_country="AU", proxy_city="SYDNEY") as browser:
        browser.page.goto(LANDING_URL, wait_until="networkidle")
        data = browser.page.evaluate(_JS_SEARCH, payload)

    results = parse_search_response(data)

    log.info(
        "GS search returned %d results for '%s' (page %d)",
        len(results),
        query,
        page,
    )
    return results


# ---------------------------------------------------------------------------
# Login — Google OAuth (same pattern as Alibaba)
# ---------------------------------------------------------------------------

# Verified from html_test_fixtures/Login _ Globalsources.com.html
GS_LOGIN_URL = "https://www.globalsources.com/member/login"

# The Google icon is inside .third-party-box, wrapped in a plain div
GOOGLE_LOGIN_ICON = ".third-party-box .ic_ic_google"


def login_globalsources(page: Page, session_url: str = "") -> None:
    """Log into GlobalSources via Google OAuth using shared Gmail credentials.

    The GS login page has Facebook/Google/LinkedIn/Twitter third-party
    buttons. We click the Google icon which opens a popup, then reuse
    the same google_login() flow as Alibaba.
    """
    try:
        page.goto(GS_LOGIN_URL, timeout=60_000)
    except PlaywrightError as exc:
        if "interrupted by another navigation" not in str(exc):
            raise
        log.info("Login page redirected (expected): %s", page.url)

    log.info("GS login page loaded, URL: %s", page.url)

    # Wait for the third-party login icons to render
    page.wait_for_selector(GOOGLE_LOGIN_ICON, timeout=30_000)

    # The icon is an <i> inside a <div> — click the parent div
    google_div = page.locator(GOOGLE_LOGIN_ICON).locator("..")
    with page.expect_popup() as popup_info:
        google_div.click()
    popup = popup_info.value
    log.info("Google popup opened: %s", popup.url)

    google_login(popup, session_url=session_url)
    log.info("Google login returned, main page URL: %s", page.url)

    # Wait for redirect away from login page
    page.wait_for_url(
        lambda url: "/member/login" not in url,
        timeout=30_000,
        wait_until="domcontentloaded",
    )
    log.info("URL after wait: %s", page.url)
    page.wait_for_timeout(2_000)
    log.info("Logged into GlobalSources as %s", settings.GMAIL_ACCOUNT)


# ---------------------------------------------------------------------------
# Inquiry submission
# ---------------------------------------------------------------------------

# Verified from html_test_fixtures/gs_inquiry_final_state.html
INQUIRY_FORM = ".inquiry-box form"
INQUIRY_TEXTAREA = "textarea.msg-input"
INQUIRY_EMAIL = ".email-box input.ant-select-search__field"
INQUIRY_SUBMIT = "button.send-btn"

PAGE_LOAD_TIMEOUT = 300_000


@stamina.retry(on=PlaywrightError, attempts=3, timeout=300)
def _load_product_page(page: Page, product_url: str) -> None:
    """Navigate to product page and wait for the inquiry form to render."""
    page.goto(product_url, timeout=PAGE_LOAD_TIMEOUT)
    page.wait_for_selector(INQUIRY_FORM, timeout=PAGE_LOAD_TIMEOUT)


def send_product_inquiry(page: Page, product_url: str, message: str) -> bool:
    """Submit an inquiry via the inline form on a GlobalSources product page.

    Returns True if the inquiry was submitted successfully.
    """
    _load_product_page(page, product_url)

    page.locator(INQUIRY_TEXTAREA).fill(message)
    log.info("Filled inquiry message on %s", product_url)

    # When logged in, email is auto-filled. When not, fill it ourselves.
    email_input = page.locator(INQUIRY_EMAIL)
    if email_input.count() > 0:
        current_val = email_input.input_value()
        if not current_val:
            email_input.fill(settings.GMAIL_ACCOUNT)
            log.info("Filled email field with %s", settings.GMAIL_ACCOUNT)

    page.click(INQUIRY_SUBMIT)
    log.info("Clicked Send Inquiry Now on %s", product_url)

    try:
        page.wait_for_selector("text=Sent Successfully", timeout=15_000)
        log.info("Inquiry confirmed for %s", product_url)
        return True
    except PlaywrightError:
        log.warning("Inquiry click fired but not confirmed for %s", product_url)
        return False


# ---------------------------------------------------------------------------
# Product page parsing
# ---------------------------------------------------------------------------

# Verified from html_test_fixtures/gs_inquiry_final_state.html
# GS uses Ant Design descriptions tables inside named sections:
#   #Product  → "Product Information" (Model Number, Brand Name, etc.)
#   #Shipping → "Shipping Details" (FOB Port, Lead Time, Weight, etc.)
#   #Payment  → "Payment Details"
# Each section has: .ant-descriptions th.ant-descriptions-item-label + td.ant-descriptions-item-content
SPEC_SECTIONS = ["#Product", "#Shipping"]
SPEC_LABEL = "th.ant-descriptions-item-label"
SPEC_VALUE = "td.ant-descriptions-item-content"

# The "Key Specifications" block uses free-text <p> tags inside .specifications
KEY_SPECS_SELECTOR = ".specifications .tpl_txt_editor"


def parse_product_specs(html: str) -> dict:
    """Extract product specifications from a GlobalSources product page.

    Returns a grouped dict matching the Alibaba format:
    {"Group Name": {"key": "value", ...}, ...}

    GS product pages use Ant Design description tables in named sections
    (#Product, #Shipping) plus a free-text "Key Specifications" block.
    """
    soup = BeautifulSoup(html, "html.parser")
    all_specs = {}

    # Structured specs from Ant Design description tables
    for section_id in SPEC_SECTIONS:
        section = soup.select_one(section_id)
        if not section:
            continue

        title_el = section.select_one(".title")
        group_name = (
            title_el.get_text(strip=True) if title_el else section_id.lstrip("#")
        )

        specs = {}
        labels = section.select(SPEC_LABEL)
        values = section.select(SPEC_VALUE)
        for label, value in zip(labels, values):
            key = label.get_text(strip=True)
            val = value.get_text(strip=True)
            if key and val:
                specs[key] = val

        if specs:
            all_specs[group_name] = specs

    # Free-text key specs (colon-separated lines in <p> tags)
    key_specs_el = soup.select_one(KEY_SPECS_SELECTOR)
    if key_specs_el:
        key_specs = {}
        for p in key_specs_el.find_all("p"):
            text = p.get_text(strip=True)
            if ":" in text:
                parts = text.split(":", 1)
                key = parts[0].strip()
                val = parts[1].strip()
                if key and val:
                    key_specs[key] = val
        if key_specs:
            all_specs["Key Specifications"] = key_specs

    if not all_specs:
        log.warning("No specs found in GS product page HTML")

    return all_specs


# FIXME: GS title suffixes — add more patterns as we see them on real pages
_TITLE_SUFFIXES = [
    " | GlobalSources",
    " - GlobalSources",
    " | Suppliers & Manufacturers",
    " - Suppliers & Manufacturers",
    " | Global Sources",
    " - Global Sources",
]


def parse_product_title(html: str) -> str:
    """Extract product title from a GlobalSources product page.

    Reads the <title> tag and strips GS-specific suffixes.
    """
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    raw = title_tag.get_text(strip=True)

    for suffix in _TITLE_SUFFIXES:
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break

    return raw


def parse_search_response(data: dict) -> list[dict]:
    """Parse the raw GS search API JSON into a flat list of offer dicts."""
    if data.get("code") != "200":
        log.warning(
            "GS search API returned code=%s: %s", data.get("code"), data.get("msg")
        )
        return []

    items = data.get("data", {}).get("list", [])
    results = []

    for item in items:
        detail_path = item.get("desktopProductDetailUrl", "")
        product_url = f"{LANDING_URL}{detail_path}" if detail_path else ""

        org_id = str(item.get("orgId", ""))
        company_name = item.get("companyName", "")

        moq_qty = item.get("minOrderQuantity")
        moq_unit = item.get("minOrderSingleUnit", "")
        moq = f"{moq_qty} {moq_unit}".strip() if moq_qty else ""

        results.append(
            {
                "product_id": str(item.get("productId", "")),
                "product_url": product_url,
                "title": _strip_html(item.get("productName", "")),
                "price": item.get("price", ""),
                "moq": moq,
                "company_name": company_name,
                "company_id": org_id,
                "profile_url": f"{LANDING_URL}/suppliers/{org_id}",
                "country": "",
                "certifications": [],
            }
        )

    return results
