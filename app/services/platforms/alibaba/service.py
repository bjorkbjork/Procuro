"""Alibaba supplier search and product page parsing. Search uses their internal
JSON API (no browser needed). Parsing functions extract specs from product page
HTML — the caller is responsible for fetching the HTML (via Browserbase etc.).
Login and inquiry submission use Playwright (via Browserbase)."""

import logging
import re
import time
from pathlib import Path

import requests
import stamina
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError, Page

from app.base.config import settings
from app.services.google_auth import google_login

log = logging.getLogger(__name__)

BASE_URL = "https://www.alibaba.com/search/api/proTextSearch"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.alibaba.com/",
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def search_suppliers(
    query: str,
    core_product: str = "",
    attributes: str = "",
    page: int = 1,
    page_size: int = 20,
) -> list[dict]:
    """Search Alibaba for verified suppliers matching a product query.

    Returns a list of dicts, each containing supplier and product info
    ready for insertion into the suppliers table.
    """
    params = {
        "assessmentCompany": "true",
        "query": query,
        "page": str(page),
        "pageSize": str(page_size),
    }
    if core_product:
        params["coreProduct"] = core_product
    if attributes:
        params["attributes"] = attributes

    resp = requests.get(BASE_URL, params=params, headers=DEFAULT_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        log.warning("Alibaba API returned success=false: %s", data.get("msgInfo"))
        return []

    offers = data.get("model", {}).get("offers", [])
    results = []

    for offer in offers:
        certs = [
            icon.get("name", "")
            for cert in offer.get("certifications", [])
            for icon in cert.get("prefixIcons", [])
            if icon.get("name")
        ]

        results.append(
            {
                "product_id": offer.get("productId"),
                "product_url": _normalize_url(offer.get("productUrl", "")),
                "title": _strip_html(offer.get("title", "")),
                "price": offer.get("price", ""),
                "moq": offer.get("moq", ""),
                "company_name": offer.get("companyName", ""),
                "company_id": offer.get("companyId", ""),
                "profile_url": _normalize_url(offer.get("supplierHref", "")),
                "home_url": _normalize_url(offer.get("supplierHomeHref", "")),
                "country": offer.get("countryCode", ""),
                "years_on_platform": offer.get("goldSupplierYears", ""),
                "review_score": offer.get("reviewScore", ""),
                "review_count": offer.get("reviewCount", ""),
                "certifications": certs,
                "sold_count": offer.get("soldOrder", ""),
            }
        )

    log.info(
        "Alibaba search returned %d results for '%s' (page %d)",
        len(results),
        query,
        page,
    )
    return results


INQUIRY_BUTTON = "[data-testid='customizationSkuSummary-INQUIRY']"
WHOLESALE_INDICATOR = "button:has-text('Start order')"
INQUIRY_IFRAME = ".alitalk-dialog-iframe"
INQUIRY_TEXTAREA = ".content-input"
INQUIRY_SUBMIT = "button.next-btn-primary"


class WholesaleProductError(Exception):
    """Product page is wholesale-only — no inquiry form available."""


def login_alibaba(page: Page, session_url: str = "") -> None:
    """Log into Alibaba via Google OAuth using the shared Gmail credentials."""
    try:
        page.goto("https://login.alibaba.com/")
    except PlaywrightError as exc:
        # Alibaba login page redirects; Playwright raises but the page is fine
        if "interrupted by another navigation" not in str(exc):
            raise
        log.info("Login page redirected (expected): %s", page.url)

    log.info("Login page loaded, URL: %s", page.url)

    page.wait_for_selector("#google a", timeout=30_000)
    with page.expect_popup() as popup_info:
        page.locator("#google a").click()
    popup = popup_info.value
    log.info("Google popup opened: %s", popup.url)

    google_login(popup, session_url=session_url)
    log.info("Google login returned, main page URL: %s", page.url)

    page.wait_for_url(
        lambda url: "login.alibaba.com" not in url,
        timeout=30_000,
        wait_until="domcontentloaded",
    )
    log.info("URL after wait: %s", page.url)
    page.wait_for_timeout(2_000)
    log.info("Logged into Alibaba as %s", settings.GMAIL_ACCOUNT)


def _get_inquiry_frame(page: Page, timeout: int = 15_000) -> "Frame":
    """Wait for the AliTalk inquiry iframe to load and return its Frame."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        frame = page.frame(url=re.compile(r"message\.alibaba\.com"))
        if frame:
            return frame
        page.wait_for_timeout(500)
    raise TimeoutError("AliTalk inquiry iframe did not load")


_JS_FILL_AND_SUBMIT = (Path(__file__).parent / "fill_and_submit.js").read_text()


PAGE_LOAD_TIMEOUT = 300_000
SUBMIT_CONFIRM_TIMEOUT = 15


def _wait_for_submit_confirmation(
    page: Page, frame_url_pattern: re.Pattern, timeout: int
) -> bool:
    """Wait for the inquiry iframe to navigate away, confirming the send."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = page.frame(url=frame_url_pattern)
        if not frame:
            return True
        try:
            ta = frame.locator(INQUIRY_TEXTAREA)
            if ta.count() == 0:
                return True
        except PlaywrightError:
            return True
        time.sleep(1)
    return False


@stamina.retry(on=PlaywrightError, attempts=3, timeout=300)
def _load_product_page(page: Page, product_url: str) -> None:
    """Navigate to product page and wait for actionable content, with retries."""
    page.goto(product_url, timeout=PAGE_LOAD_TIMEOUT)
    page.wait_for_selector(
        f"{INQUIRY_BUTTON}, {WHOLESALE_INDICATOR}",
        timeout=PAGE_LOAD_TIMEOUT,
    )


def send_product_inquiry(page: Page, product_url: str, message: str) -> bool:
    """Submit an inquiry via the Alibaba product page inquiry modal.

    Returns True if the inquiry was submitted successfully.
    Raises WholesaleProductError if the page is wholesale-only.
    """
    _load_product_page(page, product_url)

    inquiry_btn = page.locator(INQUIRY_BUTTON)
    wholesale_btn = page.locator(WHOLESALE_INDICATOR)

    if wholesale_btn.count() > 0 and inquiry_btn.count() == 0:
        raise WholesaleProductError(product_url)

    page.click(INQUIRY_BUTTON)

    iframe_pattern = re.compile(r"message\.alibaba\.com")
    frame = _get_inquiry_frame(page)
    # Wait for the iframe React app to render the textarea before running JS
    frame.wait_for_selector(INQUIRY_TEXTAREA, timeout=30_000)

    try:
        result = frame.evaluate(
            _JS_FILL_AND_SUBMIT,
            {
                "textareaSel": INQUIRY_TEXTAREA,
                "submitSel": INQUIRY_SUBMIT,
                "message": message,
            },
        )
    except PlaywrightError as exc:
        if "Execution context was destroyed" in str(exc):
            log.info("Frame navigated during submit for %s — verifying", product_url)
            return True
        raise

    if not result.get("ok"):
        log.warning(
            "Inquiry failed for %s: %s (step: %s)",
            product_url,
            result.get("reason"),
            result.get("step"),
        )
        return False

    if _wait_for_submit_confirmation(page, iframe_pattern, SUBMIT_CONFIRM_TIMEOUT):
        log.info("Inquiry confirmed for %s", product_url)
        return True

    log.warning("Inquiry click fired but not confirmed for %s", product_url)
    return False


def parse_product_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    attr_div = soup.find("div", class_="module_attribute")
    if not attr_div:
        return {}

    specs = {}
    for group in attr_div.find_all(attrs={"data-testid": "module-attribute-group"}):
        title_el = group.find(attrs={"data-testid": "module-attribute-group-title"})
        group_name = title_el.get_text(strip=True) if title_el else ""
        group_name = group_name or "Key attributes"

        group_specs = {}
        for row in group.find_all(attrs={"data-testid": "module-attribute-row"}):
            name_el = row.find(attrs={"data-testid": "module-attribute-name"})
            value_el = row.find(attrs={"data-testid": "module-attribute-value"})
            if name_el and value_el:
                key = name_el.get_text(strip=True)
                val = value_el.get_text(strip=True)
                if key and val:
                    group_specs[key] = val
        if group_specs:
            specs[group_name] = group_specs

    return specs


def parse_product_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    raw = title_tag.get_text(strip=True)
    # Alibaba titles end with " - Buy <keywords> Product on Alibaba.com"
    raw = raw.split(" - Buy ")[0].strip()
    return raw
