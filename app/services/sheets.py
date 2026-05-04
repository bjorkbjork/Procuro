"""Google Sheets client for the input/output spreadsheet. Input tab holds product
URLs and their pipeline status. Output tab holds supplier quotes, keyed by
product slug + supplier name, with upsert semantics."""

from googleapiclient.discovery import build

from app.base.config import google_settings
from app.services.google_auth import get_google_credentials

INPUT_TAB = "Input"
OUTPUT_TAB = "Output"
MATCH_RESULTS_TAB = "Match Results"

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

MATCH_RESULTS_COLUMNS = {
    "source_product": 0,
    "supplier_product": 1,
    "supplier_name": 2,
    "platform": 3,
    "status": 4,
    "confidence": 5,
    "reason": 6,
    "product_url": 7,
}

MATCH_RESULTS_HEADERS = [
    "Source Product",
    "Supplier Product",
    "Supplier",
    "Platform",
    "Status",
    "Confidence",
    "Reason",
    "Product URL",
]


class SheetsService:
    def __init__(self):
        creds = get_google_credentials()
        self.service = build("sheets", "v4", credentials=creds)
        self.spreadsheet_id = google_settings.GOOGLE_SHEET_ID

    def get_spreadsheet_metadata(self) -> dict:
        return (
            self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
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
                "url": (
                    row[INPUT_COLUMNS["url"]] if len(row) > INPUT_COLUMNS["url"] else ""
                ),
                "status": (
                    row[INPUT_COLUMNS["status"]]
                    if len(row) > INPUT_COLUMNS["status"]
                    else ""
                ),
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
        """Write columns A:J for a given sheet row (1-indexed)."""
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{OUTPUT_TAB}!A{sheet_row}:J{sheet_row}",
            valueInputOption="RAW",
            body={
                "values": [
                    [
                        data.get("source_product_title", ""),
                        data.get("source_link", ""),
                        data.get("source_slug", ""),
                        data.get("supplier_name", ""),
                        data.get("best_price_usd_fob", ""),
                        data.get("moq", ""),
                        data.get("lead_time", ""),
                        data.get("email_chain", ""),
                        data.get("last_updated_date", ""),
                        data.get("initial_outreach_date", ""),
                    ]
                ]
            },
        ).execute()

    def upsert_output_row(self, data: dict) -> None:
        rows = self.read_output_rows()
        slug = data.get("source_slug", "")
        supplier = data.get("supplier_name", "")

        for i, row in enumerate(rows):
            if row["source_slug"] == slug and row["supplier_name"] == supplier:
                self._write_output_row(i + 2, data)
                return

        sheet_row = len(rows) + 2
        self._write_output_row(sheet_row, data)

    def _ensure_tab(self, tab_name: str, headers: list[str] | None = None) -> None:
        """Create a tab if it doesn't exist. Optionally write a header row."""
        meta = self.get_spreadsheet_metadata()
        for s in meta["sheets"]:
            if s["properties"]["title"] == tab_name:
                return
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        if headers:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{tab_name}!A1:{chr(64 + len(headers))}1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()

    def sync_match_results(self, rows: list[list[str]]) -> None:
        """Overwrite the Match Results tab with the given rows (full replace)."""
        tab = MATCH_RESULTS_TAB
        self._ensure_tab(tab, MATCH_RESULTS_HEADERS)
        last_col = chr(64 + len(MATCH_RESULTS_HEADERS))

        # Clear existing data below header
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{tab}!A2:{last_col}",
        ).execute()

        if rows:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{tab}!A2:{last_col}{len(rows) + 1}",
                valueInputOption="RAW",
                body={"values": rows},
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
            body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": idx + 1,  # +1 for header
                                "endIndex": idx + 2,
                            }
                        }
                    }
                ]
            },
        ).execute()
