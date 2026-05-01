"""Alibaba supplier search via their internal JSON API. No browser session needed —
plain HTTP requests return structured product and supplier data. Uses the
proTextSearch endpoint with assessmentCompany=true for verified manufacturers."""

import logging
import re

import requests

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
