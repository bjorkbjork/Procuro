from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.alibaba.service import (
    parse_product_specs,
    parse_product_title,
    search_suppliers,
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
