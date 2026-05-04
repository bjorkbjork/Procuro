"""Supplier platform protocol. Each platform subdirectory exposes a class
called `Platform` in its __init__.py that satisfies this interface."""

from typing import Protocol

from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum


class PlatformMessage:
    """A message read from a platform's messaging inbox."""

    def __init__(
        self,
        supplier_name: str,
        message_text: str,
        conversation_url: str,
        product_url: str | None = None,
        sent_at: str | None = None,
    ):
        self.supplier_name = supplier_name
        self.message_text = message_text
        self.conversation_url = conversation_url
        self.product_url = product_url
        self.sent_at = sent_at


class SupplierPlatform(Protocol):
    platform: PlatformEnum
    spec_selector: str
    inquiry_agent_prompt: str
    messaging_agent_prompt: str

    def search(self, query: str, page_size: int = 20) -> list[dict]: ...

    def parse_specs(self, html: str) -> dict: ...

    def parse_title(self, html: str) -> str: ...

    def login(self, page: Page, session_url: str = "") -> None: ...

    def send_inquiry(self, page: Page, product_url: str, message: str) -> bool: ...

    def url_slug(self, product_url: str) -> str: ...

    def read_platform_messages(self, page: Page) -> list[PlatformMessage]: ...

    def send_platform_reply(
        self, page: Page, conversation_url: str, message: str
    ) -> bool: ...
