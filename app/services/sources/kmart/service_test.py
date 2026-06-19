"""Tests for Kmart AU product page parsing. Uses saved HTML fixture."""

import pytest

from app.base.config import PROJECT_ROOT
from app.services.sources.kmart.service import parse_specs, parse_title, slug_from_url

FIXTURES_DIR = PROJECT_ROOT / "html_test_fixtures"
FIXTURE_PATH = FIXTURES_DIR / "Smart Multimedia Projector - White - Kmart.html"


@pytest.fixture(scope="module")
def html():
    return FIXTURE_PATH.read_text()


class TestParseTitle:
    def test_extracts_product_name(self, html):
        title = parse_title(html)
        assert "Smart Multimedia Projector" in title

    def test_strips_kmart_suffix(self, html):
        title = parse_title(html)
        assert "Kmart" not in title

    def test_empty_html_returns_empty(self):
        assert parse_title("<html></html>") == ""

    def test_falls_back_to_title_tag(self):
        html = "<html><head><title>Some Product - Kmart</title></head></html>"
        assert parse_title(html) == "Some Product"


class TestParseSpecs:
    def test_returns_grouped_specs(self, html):
        specs = parse_specs(html)
        assert isinstance(specs, dict)
        assert len(specs) >= 1

    def test_has_product_details(self, html):
        specs = parse_specs(html)
        assert "Product Details" in specs

    def test_extracts_dimensions(self, html):
        specs = parse_specs(html)
        details = specs["Product Details"]
        assert "Dimensions/Size" in details or "Power source" in details

    def test_has_additional_info(self, html):
        specs = parse_specs(html)
        assert "Additional Information" in specs or "Warranty" in specs

    def test_empty_html_returns_empty(self):
        assert parse_specs("<html></html>") == {}


class TestSlugFromUrl:
    def test_extracts_slug(self):
        url = "https://www.kmart.com.au/product/smart-multimedia-projector-white-43520831/"
        assert slug_from_url(url) == "smart-multimedia-projector-white-43520831"

    def test_no_trailing_slash(self):
        url = "https://www.kmart.com.au/product/smart-multimedia-projector-white-43520831"
        assert slug_from_url(url) == "smart-multimedia-projector-white-43520831"
