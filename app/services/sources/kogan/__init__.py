from app.services.sources.kogan.service import (
    parse_specs,
    parse_title,
    slug_from_url,
)


class Source:
    name = "kogan"
    proxy_country = "AU"
    proxy_city = "SYDNEY"
    spec_selector = None

    def parse_title(self, html: str) -> str:
        return parse_title(html)

    def parse_specs(self, html: str) -> dict:
        return parse_specs(html)

    def slug_from_url(self, url: str) -> str:
        return slug_from_url(url)
