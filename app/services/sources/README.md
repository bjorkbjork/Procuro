# Source Marketplaces

Each subdirectory is a retailer source (Kogan, Kmart, etc.) — the retailer whose products we're trying to find suppliers for. Sources are auto-discovered at runtime — no manual registration needed.

## Adding a new source

1. **Create the directory** `app/services/sources/newsource/` with:

   - **`service.py`** — pure functions that take raw HTML and return parsed data
   - **`service_test.py`** — tests using saved HTML fixtures in `html_test_fixtures/`
   - **`__init__.py`** — expose a class named `Source` that satisfies the `MarketplaceSource` protocol

2. **The `Source` class** must implement:
   ```python
   class Source:
       name = "newsource"          # must appear in the product URL for auto-matching
       proxy_country = "US"        # Browserbase geo-proxy, or None
       spec_selector = None        # CSS selector waited on before parsing, or None

       def parse_title(self, html: str) -> str: ...
       def parse_specs(self, html: str) -> dict: ...
       def slug_from_url(self, url: str) -> str: ...
   ```

   See `source.py` for the full protocol definition and `kogan/` for a working example.

3. **No other changes needed.** Stage 1 resolves the correct source via `get_source_for_url(url)`, which matches `source.name` against the URL. Parsing and browser config are handled automatically.

## How URL matching works

`get_source_for_url(url)` checks if `source.name` appears anywhere in the URL (case-insensitive). Make sure `name` is specific enough to avoid false matches.
