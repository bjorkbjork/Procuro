import logging

from app.agent.stage_one_spec_extraction import extract_specs
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.services.sheets import SheetsService

log = logging.getLogger(__name__)


def process_input_sheet():
    sheets = SheetsService()
    rows = sheets.read_input_rows()

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

        sheets.update_input_status(i, "processing")
        try:
            product = extract_specs(url)
            log.info("Extracted specs for %s: %s", product.slug, product.title)
            sheets.update_input_status(i, "done")
        except Exception:
            log.exception("Failed to extract specs for %s", url)
            sheets.update_input_status(i, "error")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    process_input_sheet()
