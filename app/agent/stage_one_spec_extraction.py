import logging

from bs4 import BeautifulSoup

from app.db.database import SessionLocal
from app.db.models.product import Product
from app.services.browser import BrowserSession

log = logging.getLogger(__name__)


def parse_title(soup: BeautifulSoup) -> str:
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    raw = title_tag.get_text(strip=True)
    raw = raw.removeprefix("Buy ").removesuffix(" | Kogan.com")
    return raw.split(" Online")[0].strip()


def parse_specs(soup: BeautifulSoup) -> dict:
    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary or summary.get_text(strip=True) != "Specifications":
            continue
        specs = {}
        for group in details.find_all("div", class_="mb-sm"):
            heading = group.find("h5")
            group_name = heading.get_text(strip=True) if heading else "General"
            group_specs = {}
            for row in group.find_all("div", class_="flex"):
                spans = row.find_all("span")
                if len(spans) == 2:
                    key = spans[0].get_text(strip=True)
                    val = spans[1].get_text(strip=True)
                    group_specs[key] = val
            if group_specs:
                specs[group_name] = group_specs
        return specs
    return {}


def fetch_page_html(source_url: str) -> str:
    # Retailer sites detect non-AU traffic as bot, and throw captchas
    with BrowserSession(proxy_country="AU") as s:
        s.page.goto(source_url, timeout=60_000)
        s.page.wait_for_timeout(5_000)
        html = s.page.content()
    if "captcha-delivery" in html:
        raise RuntimeError(f"Blocked by captcha on {source_url}")
    return html


def extract_specs(source_url: str) -> Product:
    """Stage 1: Extract and store Kogan specs given a product URL"""
    log.info("Extracting specs from %s", source_url)
    html = fetch_page_html(source_url)
    soup = BeautifulSoup(html, "html.parser")
    title = parse_title(soup)
    specs = parse_specs(soup)

    if not title:
        raise ValueError(f"Could not parse product title from {source_url}")
    if not specs:
        raise ValueError(f"Could not parse specifications from {source_url}")

    with SessionLocal() as session:
        product = session.query(Product).filter_by(source_url=source_url).first()
        if product:
            product.title = title
            product.specs = specs
        else:
            product = Product(source_url=source_url, title=title, specs=specs)
            session.add(product)
        session.commit()
        session.refresh(product)
        log.info(
            "Stored product %s: %s (%d spec groups)", product.id, title, len(specs)
        )
        return product
