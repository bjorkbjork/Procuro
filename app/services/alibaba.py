"""Alibaba supplier search and product page parsing. Search uses their internal
JSON API (no browser needed). Parsing functions extract specs from product page
HTML — the caller is responsible for fetching the HTML (via Browserbase etc.)."""

import logging
import re

import requests
from bs4 import BeautifulSoup

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


