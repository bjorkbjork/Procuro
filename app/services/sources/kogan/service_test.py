"""Tests for Kogan product page parsing. Uses saved HTML fixture."""

import pytest

from app.base.config import PROJECT_ROOT
from app.services.sources.kogan.service import parse_specs, parse_title, slug_from_url

FIXTURES_DIR = PROJECT_ROOT / "html_test_fixtures"
FIXTURE_PATH = (
    FIXTURES_DIR
    / "Buy Kogan 75_ QLED 4K Smart AI Google TV - Q97T Online _ Kogan.com.html"
)


@pytest.fixture(scope="module")
def html():
    return FIXTURE_PATH.read_text()


class TestParseTitle:
    def test_extracts_product_name(self, html):
        title = parse_title(html)
        assert "Kogan 75" in title
        assert "QLED" in title
        assert "Kogan.com" not in title

    def test_strips_buy_prefix(self, html):
        title = parse_title(html)
        assert not title.startswith("Buy")

    def test_empty_html_returns_empty(self):
        assert parse_title("<html></html>") == ""


class TestParseSpecs:
    def test_returns_grouped_specs(self, html):
        specs = parse_specs(html)
        assert isinstance(specs, dict)
        assert len(specs) > 5

    def test_has_display_group(self, html):
        specs = parse_specs(html)
        assert "Display" in specs
        assert 'Screen Size (")' in specs["Display"]
        assert specs["Display"]["Screen Type"] == "QLED"

    def test_has_connectivity_group(self, html):
        specs = parse_specs(html)
        assert "Connectivity" in specs
        assert "Wi-Fi" in specs["Connectivity"]

    def test_has_dimensions(self, html):
        specs = parse_specs(html)
        assert "Dimensions" in specs
        assert "Weight" in specs["Dimensions"]

    def test_empty_html_returns_empty(self):
        assert parse_specs("<html></html>") == {}


class TestSlugFromUrl:
    def test_extracts_slug(self):
        url = "https://www.kogan.com/au/buy/kogan-75-qled-4k-tv/"
        assert slug_from_url(url) == "kogan-75-qled-4k-tv"

    def test_no_trailing_slash(self):
        url = "https://www.kogan.com/au/buy/kogan-75-qled-4k-tv"
        assert slug_from_url(url) == "kogan-75-qled-4k-tv"
