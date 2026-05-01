"""Stage 2: Supplier search and matching. Split into two phases so fast
deterministic browser work doesn't block on LLM latency:

  1. search_and_extract — generate queries, search Alibaba, fetch each product
     page via Browserbase, persist to supplier_products.
  2. match_candidates — run the fuzzy-match agent over each source/supplier
     product pair, create supplier_threads for matches.
"""

import logging

from app.agent.match_agent import MatchResult, compare_products
from app.agent.query_agent import generate_search_queries
from app.db.database import SessionLocal
from app.db.models.enums import Platform
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.services.alibaba import parse_product_specs, parse_product_title, search_suppliers
from app.services.browser import BrowserSession

log = logging.getLogger(__name__)

MATCH_CONFIDENCE_THRESHOLD = 0.6
MANUFACTURER_KEYWORDS = {"manufacturer", "odm", "oem", "original manufacturer"}


def _is_manufacturer(specs: dict) -> bool:
    for group in specs.values():
        supplier_type = group.get("supplier type", "").lower()
        if not supplier_type:
            continue
        if any(kw in supplier_type for kw in MANUFACTURER_KEYWORDS):
            return True
        log.info("Supplier type '%s' did not match manufacturer keywords", supplier_type)
        return False
    # Field not present at all — give them the benefit of the doubt
    return True


def _fetch_product_specs(page, product_url: str) -> dict:
    page.goto(product_url, timeout=60_000)
    try:
        page.wait_for_selector("[data-testid='module-attribute']", timeout=15_000)
    except Exception:
        log.warning("No key-attributes table found on %s", product_url)
        return {}
    html = page.content()
    return {
        "title": parse_product_title(html),
        "specs": parse_product_specs(html),
    }


def _upsert_supplier(session, offer: dict) -> Supplier:
    supplier = session.query(Supplier).filter_by(
        profile_url=offer["profile_url"],
    ).first()
    if supplier:
        return supplier
    supplier = Supplier(
        name=offer["company_name"],
        platform=Platform.ALIBABA,
        profile_url=offer["profile_url"],
        is_verified=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def search_and_extract(source_product_id: int) -> list[SupplierProduct]:
    """Phase 1: Search Alibaba, scrape product pages, persist supplier products."""
    with SessionLocal() as session:
        source = session.get(SourceProduct, source_product_id)
        if not source:
            raise ValueError(f"SourceProduct {source_product_id} not found")
        title = source.title
        specs = source.specs

    if not specs:
        raise ValueError(f"SourceProduct {source_product_id} has no specs — run Stage 1 first")

    queries = generate_search_queries(title, specs)
    log.info("Generated %d search queries for '%s'", len(queries), title)

    offers = []
    seen_product_ids = set()
    for query in queries:
        for offer in search_suppliers(query, page_size=10):
            pid = offer["product_id"]
            if pid not in seen_product_ids:
                seen_product_ids.add(pid)
                offers.append(offer)

    log.info("Found %d unique offers across %d queries", len(offers), len(queries))
    if not offers:
        return []

    saved = []
    with BrowserSession() as browser:
        for offer in offers:
            product_url = offer["product_url"]

            with SessionLocal() as session:
                existing = session.query(SupplierProduct).filter_by(
                    product_url=product_url,
                ).first()
                if existing:
                    log.info("Already extracted %s — skipping", product_url)
                    saved.append(existing)
                    continue

            log.info("Fetching specs from %s", product_url)
            details = _fetch_product_specs(browser.page, product_url)
            if not details or not details.get("specs"):
                log.warning("No specs extracted for %s — skipping", product_url)
                continue

            if not _is_manufacturer(details["specs"]):
                log.info("Not a manufacturer — skipping %s", product_url)
                continue

            with SessionLocal() as session:
                supplier = _upsert_supplier(session, offer)
                sp = SupplierProduct(
                    source_product_id=source_product_id,
                    supplier_id=supplier.id,
                    platform=Platform.ALIBABA,
                    product_url=product_url,
                    title=details["title"] or offer["title"],
                    specs=details["specs"],
                    price=offer.get("price", ""),
                    moq=offer.get("moq", ""),
                )
                session.add(sp)
                session.commit()
                session.refresh(sp)
                saved.append(sp)

    log.info("Extracted %d/%d supplier products for '%s'", len(saved), len(offers), title)
    return saved


def match_candidates(source_product_id: int) -> list[SupplierThread]:
    """Phase 2: Fuzzy-match each supplier product against the source product."""
    with SessionLocal() as session:
        source = session.get(SourceProduct, source_product_id)
        if not source:
            raise ValueError(f"SourceProduct {source_product_id} not found")
        title = source.title
        specs = source.specs

        candidates = session.query(SupplierProduct).filter_by(
            source_product_id=source_product_id,
        ).all()

    if not candidates:
        log.info("No supplier products to match for '%s'", title)
        return []

    log.info("Matching %d candidates against '%s'", len(candidates), title)
    threads = []

    for candidate in candidates:
        result: MatchResult = compare_products(
            reference_title=title,
            reference_specs=specs,
            candidate_title=candidate.title,
            candidate_details=candidate.specs,
        )

        log.info(
            "Match '%s': match=%s confidence=%.2f",
            candidate.title[:60], result.is_match, result.confidence,
        )

        if not result.is_match or result.confidence < MATCH_CONFIDENCE_THRESHOLD:
            continue

        with SessionLocal() as session:
            existing = session.query(SupplierThread).filter_by(
                source_product_id=source_product_id,
                supplier_product_id=candidate.id,
            ).first()
            if existing:
                threads.append(existing)
                continue

            thread = SupplierThread(
                source_product_id=source_product_id,
                supplier_product_id=candidate.id,
                supplier_id=candidate.supplier_id,
                state="NEW",
            )
            session.add(thread)
            session.commit()
            session.refresh(thread)
            threads.append(thread)

    log.info("%d/%d matched for '%s'", len(threads), len(candidates), title)
    return threads


def run_supplier_search(source_product_id: int) -> list[SupplierThread]:
    """Full Stage 2 pipeline: extract then match."""
    search_and_extract(source_product_id)
    return match_candidates(source_product_id)
