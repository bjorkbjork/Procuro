"""GlobalSources supplier search via their internal JSON API. The API sits
behind Incapsula bot protection, so requests are routed through a Browserbase
session whose browser has already passed the JS challenge."""

import logging
import re

from app.services.browser import BrowserSession

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
