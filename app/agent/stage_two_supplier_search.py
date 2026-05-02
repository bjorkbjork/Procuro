"""Stage 2: Supplier search and matching. Split into two phases so fast
deterministic browser work doesn't block on LLM latency:

  1. search_and_extract — generate queries, search all registered platforms,
     fetch each product page via Browserbase (threaded), persist to
     supplier_products.
  2. match_candidates — run the fuzzy-match agent (threaded) over each
     source/supplier product pair, create supplier_threads for matches.

run_supplier_search loops search → match until matches are found or the
candidate limit is reached.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.agent.match_agent import MatchResult, compare_products
from app.agent.query_agent import generate_search_queries
from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.db.models.supplier import Supplier
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.services.browser import BrowserSession
from app.services.platforms import get_platforms
from app.services.platforms.platform import SupplierPlatform

log = logging.getLogger(__name__)

MATCH_CONFIDENCE_THRESHOLD = 0.6
MANUFACTURER_KEYWORDS = {"manufacturer", "odm", "oem", "original manufacturer"}
MAX_CANDIDATES = 100


def _is_manufacturer(specs: dict) -> bool:
    for group in specs.values():
        supplier_type = group.get("supplier type", "").lower()
        if not supplier_type:
            continue
        if any(kw in supplier_type for kw in MANUFACTURER_KEYWORDS):
            return True
        log.info("Supplier type '%s' did not match manufacturer keywords", supplier_type)
        return False
    return True


def _fetch_product_specs(page, platform: SupplierPlatform, product_url: str) -> dict:
    page.goto(product_url, timeout=60_000)
    if platform.spec_selector:
        try:
            page.wait_for_selector(platform.spec_selector, timeout=15_000)
        except Exception:
            log.warning("Spec selector not found on %s", product_url)
            return {}
    html = page.content()
    return {
        "title": platform.parse_title(html),
        "specs": platform.parse_specs(html),
    }


def _upsert_supplier(session, offer: dict, platform: SupplierPlatform) -> Supplier:
    supplier = session.query(Supplier).filter_by(
        profile_url=offer["profile_url"],
    ).first()
    if supplier:
        return supplier
    supplier = Supplier(
        name=offer["company_name"],
        platform=platform.platform,
        profile_url=offer["profile_url"],
        is_verified=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def _fetch_and_save_offer(
    source_product_id: int, offer: dict, platform: SupplierPlatform,
    thread_name: str = "",
) -> SupplierProduct | None:
    """Fetch specs for a single offer in its own browser session. Returns the
    saved SupplierProduct, or None on failure/skip."""
    if thread_name:
        threading.current_thread().name = thread_name
    product_url = offer["product_url"]

    with SessionLocal() as session:
        existing = session.query(SupplierProduct).filter_by(
            product_url=product_url,
        ).first()
        if existing:
            log.info("Already extracted %s — skipping", product_url)
            return existing

    log.info("Fetching specs from %s", product_url)
    try:
        with BrowserSession(proxy_country="AU") as browser:
            details = _fetch_product_specs(browser.page, platform, product_url)
    except Exception:
        log.exception("Failed to fetch specs from %s", product_url)
        return None

    if not details or not details.get("specs"):
        log.warning("No specs extracted for %s — skipping", product_url)
        return None

    if not _is_manufacturer(details["specs"]):
        log.info("Not a manufacturer — skipping %s", product_url)
        return None

    with SessionLocal() as session:
        supplier = _upsert_supplier(session, offer, platform)
        sp = SupplierProduct(
            source_product_id=source_product_id,
            supplier_id=supplier.id,
            platform=platform.platform,
            product_url=product_url,
            title=details["title"] or offer["title"],
            specs=details["specs"],
            price=offer.get("price", ""),
            moq=offer.get("moq", ""),
        )
        session.add(sp)
        session.commit()
        session.refresh(sp)
        return sp


def search_and_extract(
    source_product_id: int, queries: list[str] | None = None,
) -> list[SupplierProduct]:
    """Phase 1: Search all platforms, scrape product pages, persist supplier products.

    Pass queries explicitly to skip the LLM query generation step.
    """
    with SessionLocal() as session:
        source = session.get(SourceProduct, source_product_id)
        if not source:
            raise ValueError(f"SourceProduct {source_product_id} not found")
        title = source.title
        specs = source.specs

    if not specs:
        raise ValueError(f"SourceProduct {source_product_id} has no specs — run Stage 1 first")

    if queries is None:
        queries = generate_search_queries(title, specs)
    log.info("Generated %d search queries for '%s'", len(queries), title)

    platforms = get_platforms()
    saved = []

    for platform in platforms:
        offers = []
        seen_urls = set()
        for query in queries:
            for offer in platform.search(query, page_size=10):
                url = offer["product_url"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    offers.append(offer)

        log.info(
            "Found %d unique offers on %s for '%s'",
            len(offers), platform.platform.value, title,
        )
        if not offers:
            continue

        futures = {}
        with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
            for offer in offers:
                slug = platform.url_slug(offer["product_url"])
                future = pool.submit(
                    _fetch_and_save_offer,
                    source_product_id, offer, platform,
                    thread_name=slug,
                )
                futures[future] = offer["product_url"]

            for future in as_completed(futures):
                result = future.result()
                if result:
                    saved.append(result)

    log.info("Extracted %d supplier products total for '%s'", len(saved), title)
    return saved


def _match_single_candidate(
    candidate: SupplierProduct,
    source_product_id: int,
    title: str,
    specs: dict,
    match_all: bool,
) -> SupplierThread | None:
    """Match a single candidate against the source product. Returns a
    SupplierThread if matched, None otherwise."""
    threading.current_thread().name = (candidate.title or "unknown")[:40]

    if match_all:
        is_match = True
        confidence = 1.0
        result = None
    else:
        result: MatchResult = compare_products(
            reference_title=title,
            reference_specs=specs,
            candidate_title=candidate.title,
            candidate_details=candidate.specs,
        )
        is_match = result.is_match
        confidence = result.confidence

    if not is_match or confidence < MATCH_CONFIDENCE_THRESHOLD:
        log.info(
            "No match '%s': confidence=%.2f reason=%s diffs=%s",
            candidate.title[:60], confidence,
            result.reasoning, result.key_differences,
        )
        return None

    log.info(
        "Matched '%s': confidence=%.2f",
        candidate.title[:60], confidence,
    )

    with SessionLocal() as session:
        existing = session.query(SupplierThread).filter_by(
            source_product_id=source_product_id,
            supplier_product_id=candidate.id,
        ).first()
        if existing:
            return existing

        thread = SupplierThread(
            source_product_id=source_product_id,
            supplier_product_id=candidate.id,
            supplier_id=candidate.supplier_id,
            state="NEW",
        )
        session.add(thread)
        session.commit()
        session.refresh(thread)
        return thread


def match_candidates(
    source_product_id: int, match_all: bool = False,
) -> list[SupplierThread]:
    """Phase 2: Fuzzy-match each supplier product against the source product.

    Pass match_all=True to skip LLM matching and accept all candidates.
    """
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

    futures = {}
    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
        for candidate in candidates:
            future = pool.submit(
                _match_single_candidate,
                candidate, source_product_id, title, specs, match_all,
            )
            futures[future] = candidate.id

        for future in as_completed(futures):
            result = future.result()
            if result:
                threads.append(result)

    log.info("%d/%d matched for '%s'", len(threads), len(candidates), title)
    return threads


def run_supplier_search(source_product_id: int) -> list[SupplierThread]:
    """Full Stage 2 pipeline: search, extract, match — retry with new queries
    until matches are found or the candidate limit is reached."""
    attempt = 0
    while True:
        attempt += 1
        saved = search_and_extract(source_product_id)
        threads = match_candidates(source_product_id)

        if threads:
            return threads

        with SessionLocal() as session:
            total_candidates = session.query(SupplierProduct).filter_by(
                source_product_id=source_product_id,
            ).count()

        if total_candidates >= MAX_CANDIDATES:
            log.warning(
                "Reached %d candidates with no matches — giving up",
                total_candidates,
            )
            return []

        log.info(
            "No matches after attempt %d (%d candidates so far) — retrying with new queries",
            attempt, total_candidates,
        )
