"""Alibaba supplier search and product page parsing. Search uses their internal
JSON API (no browser needed). Parsing functions extract specs from product page
HTML — the caller is responsible for fetching the HTML (via Browserbase etc.).
Login and inquiry submission use Playwright (via Browserbase)."""

import logging
import re

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page

from app.base.config import settings

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

    resp = requests.get(
        BASE_URL, params=params, headers=DEFAULT_HEADERS, timeout=15
    )
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

        results.append({
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
        })

    log.info(
        "Alibaba search returned %d results for '%s' (page %d)",
        len(results), query, page,
    )
    return results


INQUIRY_BUTTON = "[data-testid='customizationSkuSummary-INQUIRY']"
INQUIRY_IFRAME = ".alitalk-dialog-iframe"
INQUIRY_TEXTAREA = ".content-input"
INQUIRY_SUBMIT = "button.next-btn-primary"


def login_alibaba(page: Page) -> None:
    """Log into Alibaba via Google OAuth using the shared Gmail credentials."""
    page.goto("https://login.alibaba.com/", wait_until="networkidle")

    google_btn = page.locator("#google a")
    google_btn.click()

    # Google OAuth form — email
    page.wait_for_selector("input[type='email']", timeout=15_000)
    page.fill("input[type='email']", settings.GMAIL_ACCOUNT)
    page.click("#identifierNext")

    # Google OAuth form — password
    page.wait_for_selector("input[type='password']:visible", timeout=15_000)
    page.fill("input[type='password']", settings.GMAIL_PASSWORD)
    page.click("#passwordNext")

    # Wait for redirect back to Alibaba
    page.wait_for_url("*alibaba.com*", timeout=30_000)
    log.info("Logged into Alibaba as %s", settings.GMAIL_ACCOUNT)


def send_product_inquiry(page: Page, product_url: str, message: str) -> bool:
    """Submit an inquiry via the Alibaba product page inquiry modal.

    Returns True if the inquiry was submitted successfully.
    """
    page.goto(product_url, timeout=60_000)
    page.wait_for_selector(INQUIRY_BUTTON, timeout=15_000)
    page.click(INQUIRY_BUTTON)

    # The modal loads an iframe from message.alibaba.com
    page.wait_for_selector(INQUIRY_IFRAME, timeout=10_000)
    iframe_el = page.locator(INQUIRY_IFRAME).element_handle()
    frame = iframe_el.content_frame()

    frame.wait_for_selector(INQUIRY_TEXTAREA, timeout=10_000)
    frame.fill(INQUIRY_TEXTAREA, message)

    # Submit button enables after text is entered
    submit = frame.locator(INQUIRY_SUBMIT)
    submit.wait_for(state="attached", timeout=5_000)
    frame.wait_for_timeout(1_000)
    submit.click()

    # Wait for the modal to close or a success indicator
    page.wait_for_timeout(3_000)
    log.info("Inquiry submitted for %s", product_url)
    return True


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


