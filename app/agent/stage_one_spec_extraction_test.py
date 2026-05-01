"""
Tests for spec extraction from Kogan product pages.
Uses saved HTML fixture — no network calls.
"""

import pytest
from pathlib import Path
from bs4 import BeautifulSoup

from app.agent.stage_one_spec_extraction import parse_title, parse_specs, extract_specs
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "html_test_fixtures"
FIXTURE_PATH = FIXTURES_DIR / "Buy Kogan 75_ QLED 4K Smart AI Google TV - Q97T Online _ Kogan.com.html"
TEST_URL = "https://www.kogan.com/au/buy/test-spec-extraction/"


@pytest.fixture(scope="module")
def soup():
    html = FIXTURE_PATH.read_text()
    return BeautifulSoup(html, "html.parser")


class TestParseTitle:
    def test_extracts_product_name(self, soup):
        title = parse_title(soup)
        assert "Kogan 75" in title
        assert "QLED" in title
        assert "Kogan.com" not in title

    def test_strips_buy_prefix(self, soup):
        title = parse_title(soup)
        assert not title.startswith("Buy")


class TestParseSpecs:
    def test_returns_grouped_specs(self, soup):
        specs = parse_specs(soup)
        assert isinstance(specs, dict)
        assert len(specs) > 5

    def test_has_display_group(self, soup):
        specs = parse_specs(soup)
        assert "Display" in specs
        assert "Screen Size (\")" in specs["Display"]
        assert specs["Display"]["Screen Type"] == "QLED"

    def test_has_connectivity_group(self, soup):
        specs = parse_specs(soup)
        assert "Connectivity" in specs
        assert "Wi-Fi" in specs["Connectivity"]

    def test_has_dimensions(self, soup):
        specs = parse_specs(soup)
        assert "Dimensions" in specs
        assert "Weight" in specs["Dimensions"]

    def test_empty_html_returns_empty(self):
        soup = BeautifulSoup("<html></html>", "html.parser")
        assert parse_specs(soup) == {}


class TestExtractSpecs:
    """Tests the full extract_specs pipeline using monkeypatched fetch."""

    def test_stores_product_in_db(self, monkeypatch):
        html = FIXTURE_PATH.read_text()
        monkeypatch.setattr(
            "app.agent.stage_one_spec_extraction.fetch_page_html", lambda url: html
        )
        product = extract_specs(TEST_URL)
        assert product.id is not None
        assert "QLED" in product.title
        assert isinstance(product.specs, dict)
        assert "Display" in product.specs

    def test_updates_existing_product(self, monkeypatch):
        html = FIXTURE_PATH.read_text()
        monkeypatch.setattr(
            "app.agent.stage_one_spec_extraction.fetch_page_html", lambda url: html
        )
        p1 = extract_specs(TEST_URL)
        p2 = extract_specs(TEST_URL)
        assert p1.id == p2.id

    def test_cleanup(self):
        with SessionLocal() as session:
            session.query(SourceProduct).filter_by(url=TEST_URL).delete()
            session.commit()
