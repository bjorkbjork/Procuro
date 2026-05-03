"""Stage 6: Update the output Google Sheet with negotiation results.

Rules:
- A row is added when initial outreach is sent (Stage 3). Price fields show
  "Awaiting Quotes" until a quote arrives.
- Rows are updated when a new best price is confirmed for that supplier.
- Sheet is sorted by date-added descending (newest at top).
- One row per supplier thread (product x supplier pair)."""

import logging

from app.db.database import SessionLocal
from app.db.models.supplier_thread import SupplierThread
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
    return count
