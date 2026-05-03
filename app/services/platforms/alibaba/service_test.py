"""Tests for Alibaba service — fixture-based parsing + live search."""

import pytest
from bs4 import BeautifulSoup

from app.base.config import PROJECT_ROOT
from app.services.platforms.alibaba.service import (
    parse_product_specs,
    parse_product_title,
    search_suppliers,
)

FIXTURES_DIR = PROJECT_ROOT / "html_test_fixtures"
ALIBABA_FIXTURE = next(FIXTURES_DIR.glob("*Advertising Players*"))


@pytest.fixture(scope="module")
def alibaba_html():
    return ALIBABA_FIXTURE.read_text()


class TestParseProductSpecs:
    def test_extracts_key_attributes(self, alibaba_html):
        specs = parse_product_specs(alibaba_html)
        assert isinstance(specs, dict)
        assert len(specs) >= 1

    def test_has_spec_values(self, alibaba_html):
        specs = parse_product_specs(alibaba_html)
        all_attrs = {}
        for group in specs.values():
            all_attrs.update(group)
        assert "specification" in all_attrs
        assert all_attrs["specification"] == "QLED display"

    def test_has_brightness(self, alibaba_html):
        specs = parse_product_specs(alibaba_html)
        all_attrs = {}
        for group in specs.values():
            all_attrs.update(group)
        assert "brightness" in all_attrs
        assert "500" in all_attrs["brightness"]

    def test_has_resolution(self, alibaba_html):
        specs = parse_product_specs(alibaba_html)
        all_attrs = {}
        for group in specs.values():
            all_attrs.update(group)
        assert "resolution" in all_attrs

    def test_packaging_group(self, alibaba_html):
        specs = parse_product_specs(alibaba_html)
        assert "Packaging and delivery" in specs
        assert "Selling Units" in specs["Packaging and delivery"]

    def test_empty_html_returns_empty(self):
        assert parse_product_specs("<html></html>") == {}


class TestParseProductTitle:
    def test_extracts_title(self, alibaba_html):
        title = parse_product_title(alibaba_html)
        assert title
        assert "Alibaba.com" not in title

    def test_empty_html_returns_empty(self):
        assert parse_product_title("<html></html>") == ""


@pytest.mark.integration
class TestSearchSuppliers:
    """Live test — hits the real Alibaba API."""

    def test_search(self):
        results = search_suppliers(
            query="75 inch QLED 4K television",
            core_product="QLED television",
            attributes="75 inch,4K",
            page_size=5,
        )

        assert len(results) > 0
        for r in results:
            assert r["product_id"]
            assert r["product_url"]
            assert r["title"]
            assert r["company_name"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
