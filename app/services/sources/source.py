"""Source marketplace protocol. Each source subdirectory exposes a class
called `Source` in its __init__.py that satisfies this interface."""

from typing import Protocol


class MarketplaceSource(Protocol):
    name: str
    proxy_country: str | None
    spec_selector: str | None

    def parse_title(self, html: str) -> str: ...

    def parse_specs(self, html: str) -> dict: ...

    def slug_from_url(self, url: str) -> str: ...
