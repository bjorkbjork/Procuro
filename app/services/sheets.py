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
    "platform": 10,
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
    "Kogan Product",
    "Supplier Product",
    "Supplier",
    "Platform",
    "Status",
    "Confidence",
    "Reason",
    "Product URL",
]

AUTOMATION_STATS_TAB = "Automation Stats"
AUTOMATION_STATS_HEADERS = [
    "Stage",
    "Action",
    "Outcome",
    "Count",
    "Latest",
]

DASHBOARD_TAB = "Dashboard"
DASHBOARD_HEADERS = ["Section", "Metric", "Value", "Notes"]

ACTIVE_THREADS_TAB = "Active Threads"
ACTIVE_THREADS_HEADERS = [
    "Thread ID",
    "Product",
    "Kogan URL",
    "Supplier",
    "Platform",
    "Channel",
    "State",
    "Days Since Outreach",
    "Messages Sent",
    "Messages Received",
    "Quotes Received",
    "Latest Quote USD",
    "Best Quote USD",
    "Negotiation Rounds",
    "Respond After",
    "Link",
]

PRODUCTS_PIPELINE_TAB = "Products Pipeline"
PRODUCTS_PIPELINE_HEADERS = [
    "Product",
    "Kogan URL",
    "Candidates Found",
    "Matched",
    "Rejected",
    "Pending",
    "Active Threads",
    "Threads with Quote",
    "Best Quote USD",
    "Input Status",
]

THREAD_ACTIVITY_TAB = "Thread Activity"
THREAD_ACTIVITY_HEADERS = [
    "Timestamp",
    "Stage",
    "Action",
    "Outcome",
    "Thread ID",
    "Product",
    "Supplier",
    "Channel",
    "Detail",
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
            .get(spreadsheetId=self.spreadsheet_id, range=f"{OUTPUT_TAB}!A2:K")
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
            range=f"{OUTPUT_TAB}!A{sheet_row}:K{sheet_row}",
            valueInputOption="RAW",
            body={"values": [[data.get(k, "") for k in OUTPUT_COLUMNS]]},
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

    def sync_output_rows(self, all_data: list[dict]) -> None:
        """Read sheet once, upsert all rows in memory, write back in one call."""
        existing = self.read_output_rows()
        index: dict[tuple[str, str], int] = {}
        for i, row in enumerate(existing):
            index[(row["source_slug"], row["supplier_name"])] = i

        keys = list(OUTPUT_COLUMNS.keys())
        rows = [[row.get(k, "") for k in keys] for row in existing]

        for data in all_data:
            values = [data.get(k, "") for k in keys]
            key = (data.get("source_slug", ""), data.get("supplier_name", ""))
            if key in index:
                rows[index[key]] = values
            else:
                index[key] = len(rows)
                rows.append(values)

        last_col = chr(64 + len(keys))
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{OUTPUT_TAB}!A2:{last_col}",
        ).execute()
        if rows:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{OUTPUT_TAB}!A2:{last_col}{len(rows) + 1}",
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()

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

    def _sync_full_tab(self, tab: str, headers: list[str], rows: list[list]) -> None:
        self._ensure_tab(tab, headers)
        last_col = chr(64 + len(headers))
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

    def sync_match_results(self, rows: list[list[str]]) -> None:
        self._sync_full_tab(MATCH_RESULTS_TAB, MATCH_RESULTS_HEADERS, rows)

    def sync_automation_stats(self, rows: list[list[str]]) -> None:
        self._sync_full_tab(AUTOMATION_STATS_TAB, AUTOMATION_STATS_HEADERS, rows)

    def sync_dashboard(self, rows: list[list]) -> None:
        self._sync_full_tab(DASHBOARD_TAB, DASHBOARD_HEADERS, rows)

    def sync_active_threads(self, rows: list[list]) -> None:
        self._sync_full_tab(ACTIVE_THREADS_TAB, ACTIVE_THREADS_HEADERS, rows)

    def sync_products_pipeline(self, rows: list[list]) -> None:
        self._sync_full_tab(PRODUCTS_PIPELINE_TAB, PRODUCTS_PIPELINE_HEADERS, rows)

    def sync_thread_activity(self, rows: list[list]) -> None:
        self._sync_full_tab(THREAD_ACTIVITY_TAB, THREAD_ACTIVITY_HEADERS, rows)

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
