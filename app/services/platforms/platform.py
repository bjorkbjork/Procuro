"""Supplier platform protocol. Each platform subdirectory exposes a class
called `Platform` in its __init__.py that satisfies this interface."""

from typing import Protocol

from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum


class SupplierPlatform(Protocol):
    platform: PlatformEnum
    spec_selector: str

    def search(self, query: str, page_size: int = 20) -> list[dict]: ...

    def parse_specs(self, html: str) -> dict: ...

    def parse_title(self, html: str) -> str: ...

    def login(self, page: Page, session_url: str = "") -> None: ...

    def send_inquiry(self, page: Page, product_url: str, message: str) -> bool: ...

    def url_slug(self, product_url: str) -> str: ...
