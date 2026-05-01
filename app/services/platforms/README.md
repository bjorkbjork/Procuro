# Supplier Platforms

Each subdirectory is a supplier platform (Alibaba, GlobalSources, etc.). Platforms are auto-discovered at runtime — no manual registration needed.

## Adding a new platform

1. **Add the enum value** in `app/db/models/enums.py`:
   ```python
   class Platform(StrEnum):
       ALIBABA = "alibaba"
       GLOBALSOURCES = "globalsources"
       NEWPLATFORM = "newplatform"  # add this
   ```

2. **Create the directory** `app/services/platforms/newplatform/` with:

   - **`service.py`** — pure functions for search, parsing, login, and inquiry submission
   - **`service_test.py`** — tests using saved HTML fixtures in `html_test_fixtures/`
   - **`__init__.py`** — expose a class named `Platform` that satisfies the `SupplierPlatform` protocol

3. **The `Platform` class** must implement:
   ```python
   class Platform:
       platform = PlatformEnum.NEWPLATFORM       # enum value
       spec_selector = "css-selector-or-empty"   # waited on before parsing specs

       def search(self, query: str, page_size: int = 20) -> list[dict]: ...
       def parse_specs(self, html: str) -> dict: ...
       def parse_title(self, html: str) -> str: ...
       def login(self, page: Page) -> None: ...
       def send_inquiry(self, page: Page, product_url: str, message: str) -> bool: ...
   ```

   See `platform.py` for the full protocol definition and `alibaba/` for a working example.

4. **Run a DB migration** (`alembic revision --autogenerate`) to update the platform check constraint.

5. **No other changes needed.** Stage 2 and Stage 3 will pick up the new platform automatically via `get_platforms()`.
