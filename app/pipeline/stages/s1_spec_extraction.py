import logging

from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.services.browser import BrowserSession
from app.services.sources import get_source_for_url

log = logging.getLogger(__name__)


def fetch_page_html(
    url: str, proxy_country: str | None = None, proxy_city: str | None = None
) -> str:
    with BrowserSession(proxy_country=proxy_country, proxy_city=proxy_city) as s:
        s.page.goto(url, timeout=60_000)
        s.page.wait_for_timeout(5_000)
        return s.page.content()


def extract_specs(url: str) -> SourceProduct:
    source = get_source_for_url(url)
    if not source:
        raise ValueError(f"No source registered for URL: {url}")

    log.info("Extracting specs from %s (source: %s)", url, source.name)
    html = fetch_page_html(
        url, proxy_country=source.proxy_country, proxy_city=source.proxy_city
    )
    title = source.parse_title(html)
    specs = source.parse_specs(html)

    if not title:
        raise ValueError(f"Could not parse product title from {url}")
    if not specs:
        raise ValueError(f"Could not parse specifications from {url}")

    slug = source.slug_from_url(url)

    with SessionLocal() as session:
        product = session.query(SourceProduct).filter_by(url=url).first()
        if product:
            product.title = title
            product.specs = specs
        else:
            product = SourceProduct(
                url=url,
                slug=slug,
                title=title,
                specs=specs,
            )
            session.add(product)
        session.commit()
        session.refresh(product)
        log.info(
            "Stored product %s: %s (%d spec groups)", product.id, title, len(specs)
        )
        return product
