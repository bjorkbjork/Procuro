"""Stage 6: Update the output Google Sheet with negotiation results.

Rules:
- A row is added when initial outreach is sent (Stage 3). Price fields show
  "Awaiting Quotes" until a quote arrives.
- Rows are updated when a new best price is confirmed for that supplier.
- Sheet is sorted by date-added descending (newest at top).
- One row per supplier thread (product x supplier pair)."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func

from app.db.database import SessionLocal
from app.db.models.automation_event import AutomationEvent
from app.db.models.quote import Quote
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import VALID_STATES, SupplierThread
from app.services.sheets import SheetsService

log = logging.getLogger(__name__)

GMAIL_THREAD_URL = "https://mail.google.com/mail/u/0/#inbox/{}"


def _build_row(thread: SupplierThread) -> dict:
    source = thread.source_product
    supplier = thread.supplier

    latest_quote = thread.quotes[-1] if thread.quotes else None

    first_outbound = next(
        (m for m in thread.messages if m.direction == "outbound"),
        None,
    )

    gmail_link = ""
    if thread.gmail_thread_id:
        gmail_link = GMAIL_THREAD_URL.format(thread.gmail_thread_id)

    return {
        "source_product_title": source.title,
        "source_link": source.url,
        "source_slug": source.slug,
        "supplier_name": supplier.name,
        "best_price_usd_fob": (
            f"{latest_quote.price_usd:.2f}" if latest_quote else "Awaiting Quotes"
        ),
        "moq": str(latest_quote.moq) if latest_quote and latest_quote.moq else "",
        "lead_time": latest_quote.lead_time or "" if latest_quote else "",
        "email_chain": gmail_link,
        "last_updated_date": (
            thread.last_updated.strftime("%Y-%m-%d") if thread.last_updated else ""
        ),
        "initial_outreach_date": (
            first_outbound.sent_at.strftime("%Y-%m-%d")
            if first_outbound and first_outbound.sent_at
            else ""
        ),
    }


def update_sheet() -> int:
    """Sync all non-NEW supplier threads to the output Google Sheet.

    Threads are written newest-first (by created_at desc) so the most
    recent outreach appears at the top of the sheet.

    Returns the number of rows upserted.
    """
    sheets = SheetsService()
    count = 0

    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(SupplierThread.state != "NEW")
            .order_by(SupplierThread.created_at.desc())
            .all()
        )

        for thread in threads:
            try:
                row = _build_row(thread)
                sheets.upsert_output_row(row)
                count += 1
            except Exception:
                log.exception(
                    "Failed to update sheet for thread %d (%s)",
                    thread.id,
                    thread.supplier.name,
                )

    log.info("Sheet update complete: %d rows upserted", count)

    try:
        _sync_match_results(sheets)
    except Exception:
        log.exception("Failed to sync match results tab")

    return count


def _sync_match_results(sheets: SheetsService) -> None:
    """Write all supplier product match results to the Match Results tab."""
    with SessionLocal() as session:
        products = (
            session.query(SupplierProduct)
            .order_by(
                SupplierProduct.source_product_id,
                SupplierProduct.match_status.desc(),
                SupplierProduct.match_confidence.desc(),
            )
            .all()
        )

        rows = []
        for sp in products:
            rows.append(
                [
                    sp.source_product.title,
                    sp.title,
                    sp.supplier.name,
                    sp.platform,
                    sp.match_status,
                    (
                        f"{sp.match_confidence:.2f}"
                        if sp.match_confidence is not None
                        else ""
                    ),
                    sp.match_reason or "",
                    sp.product_url,
                ]
            )

    sheets.sync_match_results(rows)
    log.info("Match results tab synced: %d rows", len(rows))


def sync_automation_stats() -> None:
    """Write automation event stats to the Automation Stats sheet tab."""
    sheets = SheetsService()
    with SessionLocal() as session:
        results = (
            session.query(
                AutomationEvent.stage,
                AutomationEvent.action,
                AutomationEvent.outcome,
                sa_func.count().label("count"),
                sa_func.max(AutomationEvent.created_at).label("latest"),
            )
            .group_by(
                AutomationEvent.stage,
                AutomationEvent.action,
                AutomationEvent.outcome,
            )
            .order_by(
                AutomationEvent.stage,
                AutomationEvent.action,
                AutomationEvent.outcome,
            )
            .all()
        )

        rows = []
        for stage, action, outcome, count, latest in results:
            rows.append(
                [
                    stage,
                    action,
                    outcome,
                    str(count),
                    latest.strftime("%Y-%m-%d %H:%M:%S") if latest else "",
                ]
            )

    sheets.sync_automation_stats(rows)
    log.info("Automation stats tab synced: %d rows", len(rows))


THREAD_ACTIVITY_LIMIT = 1000
AUTOMATION_HEALTH_DAYS = 7
TOP_ERRORS_LIMIT = 10


def sync_dashboard() -> None:
    sheets = SheetsService()
    rows = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=AUTOMATION_HEALTH_DAYS)

    with SessionLocal() as session:
        # --- Pipeline Summary ---
        total_products = session.query(sa_func.count(SourceProduct.id)).scalar()
        products_with_match = (
            session.query(
                sa_func.count(sa_func.distinct(SupplierProduct.source_product_id))
            )
            .filter(SupplierProduct.match_status == "matched")
            .scalar()
        )
        products_with_active = (
            session.query(
                sa_func.count(sa_func.distinct(SupplierThread.source_product_id))
            )
            .filter(SupplierThread.state.notin_(("CLOSED", "UNPROCESSABLE")))
            .scalar()
        )
        total_contacted = (
            session.query(sa_func.count(SupplierThread.id))
            .filter(SupplierThread.state != "NEW")
            .scalar()
        )
        threads_with_quote = session.query(
            sa_func.count(sa_func.distinct(Quote.thread_id))
        ).scalar()

        rows.append(["Pipeline Summary", "Total Products", str(total_products), ""])
        rows.append(
            [
                "Pipeline Summary",
                "Products with Matched Supplier",
                str(products_with_match),
                "",
            ]
        )
        rows.append(
            [
                "Pipeline Summary",
                "Products with Active Thread",
                str(products_with_active),
                "",
            ]
        )
        rows.append(
            ["Pipeline Summary", "Total Suppliers Contacted", str(total_contacted), ""]
        )
        rows.append(
            [
                "Pipeline Summary",
                "Threads with Quote Received",
                str(threads_with_quote),
                "",
            ]
        )

        # Avg supplier products searched / matched per source product
        searched_per_product = (
            session.query(
                SupplierProduct.source_product_id,
                sa_func.count(SupplierProduct.id).label("cnt"),
            )
            .group_by(SupplierProduct.source_product_id)
            .subquery()
        )
        avg_searched = session.query(sa_func.avg(searched_per_product.c.cnt)).scalar()

        matched_per_product = (
            session.query(
                SupplierProduct.source_product_id,
                sa_func.count(SupplierProduct.id).label("cnt"),
            )
            .filter(SupplierProduct.match_status == "matched")
            .group_by(SupplierProduct.source_product_id)
            .subquery()
        )
        avg_matched = session.query(sa_func.avg(matched_per_product.c.cnt)).scalar()
        rows.append(
            [
                "Pipeline Summary",
                "Avg Supplier Products Searched",
                f"{avg_searched:.1f}" if avg_searched else "0",
                "",
            ]
        )
        rows.append(
            [
                "Pipeline Summary",
                "Avg Supplier Products Matched",
                f"{avg_matched:.1f}" if avg_matched else "0",
                "",
            ]
        )

        # --- Thread Funnel ---
        state_counts = dict(
            session.query(SupplierThread.state, sa_func.count(SupplierThread.id))
            .group_by(SupplierThread.state)
            .all()
        )
        for state in VALID_STATES:
            rows.append(["Thread Funnel", state, str(state_counts.get(state, 0)), ""])

        # --- Automation Health (7d) ---
        health_results = (
            session.query(
                AutomationEvent.stage,
                AutomationEvent.action,
                AutomationEvent.outcome,
                sa_func.count().label("count"),
            )
            .filter(AutomationEvent.created_at >= cutoff)
            .group_by(
                AutomationEvent.stage,
                AutomationEvent.action,
                AutomationEvent.outcome,
            )
            .all()
        )
        health_by_pair: dict[tuple[str, str], Counter] = {}
        for stage, action, outcome, count in health_results:
            key = (stage, action)
            if key not in health_by_pair:
                health_by_pair[key] = Counter()
            health_by_pair[key][outcome] = count

        for (stage, action), counts in sorted(health_by_pair.items()):
            total = sum(counts.values())
            parts = []
            for outcome in ("deterministic", "agent_fallback", "failed"):
                c = counts.get(outcome, 0)
                pct = c / total * 100 if total else 0
                parts.append(f"{outcome}: {c} ({pct:.0f}%)")
            rows.append(
                [
                    f"Automation Health ({AUTOMATION_HEALTH_DAYS}d)",
                    f"{stage} / {action}",
                    "  ".join(parts),
                    str(total),
                ]
            )

        # --- Top 10 Errors (7d) ---
        total_failed_7d = (
            session.query(sa_func.count(AutomationEvent.id))
            .filter(
                AutomationEvent.created_at >= cutoff,
                AutomationEvent.outcome == "failed",
            )
            .scalar()
        ) or 0

        error_results = (
            session.query(
                AutomationEvent.detail,
                AutomationEvent.stage,
                AutomationEvent.action,
                sa_func.count().label("count"),
            )
            .filter(
                AutomationEvent.created_at >= cutoff,
                AutomationEvent.outcome == "failed",
                AutomationEvent.detail.isnot(None),
            )
            .group_by(
                AutomationEvent.detail,
                AutomationEvent.stage,
                AutomationEvent.action,
            )
            .order_by(sa_func.count().desc())
            .limit(TOP_ERRORS_LIMIT)
            .all()
        )
        for detail, stage, action, count in error_results:
            pct = count / total_failed_7d * 100 if total_failed_7d else 0
            rows.append(
                [
                    f"Top Errors ({AUTOMATION_HEALTH_DAYS}d)",
                    f"{stage} / {action}",
                    f"{count} ({pct:.1f}% of failures)",
                    detail,
                ]
            )

        # --- Quote Stats ---
        avg_rounds = (
            session.query(sa_func.avg(SupplierThread.negotiation_rounds))
            .filter(SupplierThread.negotiation_rounds > 0)
            .scalar()
        )
        total_quotes = session.query(sa_func.count(Quote.id)).scalar()
        rows.append(
            [
                "Quote Stats",
                "Avg Negotiation Rounds",
                f"{avg_rounds:.1f}" if avg_rounds else "0",
                "",
            ]
        )
        rows.append(["Quote Stats", "Total Quotes Received", str(total_quotes), ""])

    # --- Last Sync ---
    rows.append(["Last Sync", "Timestamp", now.strftime("%Y-%m-%d %H:%M:%S UTC"), ""])

    sheets.sync_dashboard(rows)
    log.info("Dashboard tab synced: %d rows", len(rows))


def sync_active_threads() -> None:
    sheets = SheetsService()
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter(SupplierThread.state.notin_(("CLOSED", "UNPROCESSABLE")))
            .order_by(SupplierThread.last_updated.desc())
            .all()
        )

        rows = []
        for t in threads:
            first_outbound = next(
                (m for m in t.messages if m.direction == "outbound"), None
            )
            days_since = ""
            if first_outbound and first_outbound.sent_at:
                days_since = str((now - first_outbound.sent_at).days)

            msgs_sent = sum(1 for m in t.messages if m.direction == "outbound")
            msgs_received = sum(1 for m in t.messages if m.direction == "inbound")
            quotes_received = len(t.quotes)
            latest_quote = f"{t.quotes[-1].price_usd:.2f}" if t.quotes else ""
            best_quote = f"{min(q.price_usd for q in t.quotes):.2f}" if t.quotes else ""

            link = ""
            if t.gmail_thread_id:
                link = GMAIL_THREAD_URL.format(t.gmail_thread_id)
            elif t.platform_thread_url:
                link = t.platform_thread_url

            respond_after = ""
            if t.respond_after:
                respond_after = t.respond_after.strftime("%Y-%m-%d %H:%M:%S")

            rows.append(
                [
                    str(t.id),
                    t.source_product.title,
                    t.source_product.url,
                    t.supplier.name,
                    t.supplier.platform,
                    t.channel,
                    t.state,
                    days_since,
                    str(msgs_sent),
                    str(msgs_received),
                    str(quotes_received),
                    latest_quote,
                    best_quote,
                    str(t.negotiation_rounds),
                    respond_after,
                    link,
                ]
            )

    sheets.sync_active_threads(rows)
    log.info("Active threads tab synced: %d rows", len(rows))


def sync_products_pipeline() -> None:
    sheets = SheetsService()

    input_status_map: dict[str, str] = {}
    try:
        for row in sheets.read_input_rows():
            if row["url"]:
                input_status_map[row["url"]] = row["status"]
    except Exception:
        log.exception("Failed to read input rows for products pipeline")

    with SessionLocal() as session:
        products = (
            session.query(SourceProduct).order_by(SourceProduct.created_at.desc()).all()
        )

        rows = []
        for p in products:
            candidates = (
                session.query(SupplierProduct)
                .filter(SupplierProduct.source_product_id == p.id)
                .all()
            )
            status_counts = Counter(c.match_status for c in candidates)

            threads = (
                session.query(SupplierThread)
                .filter(SupplierThread.source_product_id == p.id)
                .all()
            )
            active_threads = [
                t for t in threads if t.state not in ("CLOSED", "UNPROCESSABLE")
            ]
            threads_with_quote = [t for t in threads if t.quotes]

            best_quote = ""
            all_prices = [q.price_usd for t in threads for q in t.quotes]
            if all_prices:
                best_quote = f"{min(all_prices):.2f}"

            rows.append(
                [
                    p.title,
                    p.url,
                    str(len(candidates)),
                    str(status_counts.get("matched", 0)),
                    str(status_counts.get("rejected", 0)),
                    str(status_counts.get("pending", 0)),
                    str(len(active_threads)),
                    str(len(threads_with_quote)),
                    best_quote,
                    input_status_map.get(p.url, ""),
                ]
            )

    sheets.sync_products_pipeline(rows)
    log.info("Products pipeline tab synced: %d rows", len(rows))


def sync_thread_activity() -> None:
    sheets = SheetsService()

    with SessionLocal() as session:
        events = (
            session.query(AutomationEvent)
            .order_by(AutomationEvent.created_at.desc())
            .limit(THREAD_ACTIVITY_LIMIT)
            .all()
        )

        # Batch-load threads to avoid N+1
        thread_ids = {e.supplier_thread_id for e in events if e.supplier_thread_id}
        threads_by_id: dict[int, SupplierThread] = {}
        if thread_ids:
            thread_rows = (
                session.query(SupplierThread)
                .filter(SupplierThread.id.in_(thread_ids))
                .all()
            )
            threads_by_id = {t.id: t for t in thread_rows}

        rows = []
        for e in events:
            thread = (
                threads_by_id.get(e.supplier_thread_id)
                if e.supplier_thread_id
                else None
            )
            rows.append(
                [
                    e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
                    e.stage,
                    e.action,
                    e.outcome,
                    str(e.supplier_thread_id) if e.supplier_thread_id else "",
                    thread.source_product.title if thread else "",
                    thread.supplier.name if thread else "",
                    thread.channel if thread else "",
                    e.detail or "",
                ]
            )

    sheets.sync_thread_activity(rows)
    log.info("Thread activity tab synced: %d rows", len(rows))
