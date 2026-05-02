from playwright.sync_api import Page

from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.alibaba.service import (
    login_alibaba,
    parse_product_specs,
    parse_product_title,
    search_suppliers,
    send_product_inquiry,
)


class Platform:
    platform = PlatformEnum.ALIBABA
    spec_selector = "[data-testid='module-attribute']"

    def search(self, query: str, page_size: int = 20) -> list[dict]:
        return search_suppliers(query, page_size=page_size)

    def parse_specs(self, html: str) -> dict:
        return parse_product_specs(html)

    def parse_title(self, html: str) -> str:
        return parse_product_title(html)

    def login(self, page: Page, session_url: str = "") -> None:
        login_alibaba(page, session_url=session_url)

    def send_inquiry(self, page: Page, product_url: str, message: str) -> bool:
        return send_product_inquiry(page, product_url, message)

    def url_slug(self, product_url: str) -> str:
        return product_url.split("/")[-1].split("?")[0]
