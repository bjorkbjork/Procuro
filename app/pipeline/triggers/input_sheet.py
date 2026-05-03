import logging

from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.services.sheets import SheetsService

log = logging.getLogger(__name__)


def get_new_urls() -> list[dict]:
    """Poll input sheet for unprocessed URLs.

    Returns list of {"row_index": int, "url": str} for rows that need processing.
    Rows already in the DB are marked "done" and skipped.
    """
    sheets = SheetsService()
    rows = sheets.read_input_rows()
    pending: list[dict] = []

    for i, row in enumerate(rows):
        url = row["url"].strip()
        status = row["status"].strip().lower()

        if not url or status in ("processing", "done", "error"):
            continue

        with SessionLocal() as session:
            exists = session.query(SourceProduct).filter_by(url=url).first()
            if exists:
                if status != "done":
                    sheets.update_input_status(i, "done")
                continue

        pending.append({"row_index": i, "url": url})

    return pending
