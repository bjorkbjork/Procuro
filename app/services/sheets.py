"""Google Sheets client for the input/output spreadsheet. Input tab holds product
URLs and their pipeline status. Output tab holds supplier quotes, keyed by
product slug + supplier name, with upsert semantics."""

from googleapiclient.discovery import build

from app.base.config import google_settings
from app.services.google_auth import get_google_credentials

INPUT_TAB = "Input"
OUTPUT_TAB = "Output"

INPUT_COLUMNS = {"url": 0, "status": 1}

OUTPUT_COLUMNS = {
    "source_product_title": 0,
    "source_link": 1,
    "source_slug": 2,
    "supplier_name": 3,
    "best_price_usd_fob": 4,
    "moq": 5,
    "lead_time": 6,
    "email_chain": 7,
    "last_updated_date": 8,
    "initial_outreach_date": 9,
}


class SheetsService:
    def __init__(self):
        creds = get_google_credentials()
        self.service = build("sheets", "v4", credentials=creds)
        self.spreadsheet_id = google_settings.GOOGLE_SHEET_ID

    def get_spreadsheet_metadata(self) -> dict:
        return (
            self.service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id)
            .execute()
        )

    def read_input_rows(self) -> list[dict]:
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{INPUT_TAB}!A2:B")
            .execute()
        )
        rows = result.get("values", [])
        return [
            {
                "url": row[INPUT_COLUMNS["url"]] if len(row) > INPUT_COLUMNS["url"] else "",
                "status": row[INPUT_COLUMNS["status"]] if len(row) > INPUT_COLUMNS["status"] else "",
            }
            for row in rows
        ]

    def update_input_status(self, row_index: int, status: str) -> None:
        cell = f"{INPUT_TAB}!B{row_index + 2}"
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=cell,
            valueInputOption="RAW",
            body={"values": [[status]]},
        ).execute()

    def read_output_rows(self) -> list[dict]:
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{OUTPUT_TAB}!A2:J")
            .execute()
        )
        rows = result.get("values", [])
        keys = list(OUTPUT_COLUMNS.keys())
        return [
            {keys[i]: (row[i] if i < len(row) else "") for i in range(len(keys))}
            for row in rows
        ]

    def _find_output_row(self, slug: str, supplier_name: str) -> int | None:
        """Return 0-based index of matching row, or None."""
        rows = self.read_output_rows()
        for i, row in enumerate(rows):
            if row["source_slug"] == slug and row["supplier_name"] == supplier_name:
                return i
        return None

    def _write_output_row(self, sheet_row: int, data: dict) -> None:
        """Write columns A:B and D:J for a given sheet row (1-indexed)."""
        vals = self.service.spreadsheets().values()
        # A:B (title, link)
        vals.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{OUTPUT_TAB}!A{sheet_row}:B{sheet_row}",
            valueInputOption="RAW",
            body={"values": [[
                data.get("source_product_title", ""),
                data.get("source_link", ""),
            ]]},
        ).execute()
        # D:J (supplier_name through initial_outreach_date), skipping C (slug formula)
        vals.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{OUTPUT_TAB}!D{sheet_row}:J{sheet_row}",
            valueInputOption="RAW",
            body={"values": [[
                data.get("supplier_name", ""),
                data.get("best_price_usd_fob", ""),
                data.get("moq", ""),
                data.get("lead_time", ""),
                data.get("email_chain", ""),
                data.get("last_updated_date", ""),
                data.get("initial_outreach_date", ""),
            ]]},
        ).execute()

    def upsert_output_row(self, data: dict) -> None:
        rows = self.read_output_rows()
        link = data.get("source_link", "")
        supplier = data.get("supplier_name", "")
        # Derive slug the same way the sheet formula does
        slug = link.split("/buy/", 1)[1] if "/buy/" in link else ""

        for i, row in enumerate(rows):
            if row["source_slug"] == slug and row["supplier_name"] == supplier:
                self._write_output_row(i + 2, data)
                return

        # Append: write to the next empty row and set the slug formula
        sheet_row = len(rows) + 2
        self._write_output_row(sheet_row, data)
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{OUTPUT_TAB}!C{sheet_row}",
            valueInputOption="USER_ENTERED",
            body={"values": [[
                f'=MID(B{sheet_row}, SEARCH("/buy/",B{sheet_row}) + 5, LEN(B{sheet_row}))'
            ]]},
        ).execute()

    def delete_output_row(self, slug: str, supplier_name: str) -> None:
        idx = self._find_output_row(slug, supplier_name)
        if idx is None:
            return
        meta = self.get_spreadsheet_metadata()
        sheet_id = None
        for s in meta["sheets"]:
            if s["properties"]["title"] == OUTPUT_TAB:
                sheet_id = s["properties"]["sheetId"]
                break
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": idx + 1,  # +1 for header
                        "endIndex": idx + 2,
                    }
                }
            }]},
        ).execute()
