"""Tests for the extract_specs pipeline. Uses saved HTML fixture — no network calls.
Parsing tests live in app/services/sources/kogan/service_test.py."""

import pytest

from app.base.config import PROJECT_ROOT
from app.db import database as _db
from app.db.models.source_product import SourceProduct
from app.pipeline.stages.s1_spec_extraction import extract_specs

FIXTURES_DIR = PROJECT_ROOT / "html_test_fixtures"
FIXTURE_PATH = (
    FIXTURES_DIR
    / "Buy Kogan 75_ QLED 4K Smart AI Google TV - Q97T Online _ Kogan.com.html"
)
TEST_URL = "https://www.kogan.com/au/buy/test-spec-extraction/"


class TestExtractSpecs:
    """Tests the full extract_specs pipeline using monkeypatched fetch."""

    def test_stores_product_in_db(self, monkeypatch):
        html = FIXTURE_PATH.read_text()
        monkeypatch.setattr(
            "app.pipeline.stages.s1_spec_extraction.fetch_page_html",
            lambda url, proxy_country=None: html,
        )
        product = extract_specs(TEST_URL)
        assert product.id is not None
        assert "QLED" in product.title
        assert isinstance(product.specs, dict)
        assert "Display" in product.specs

    def test_updates_existing_product(self, monkeypatch):
        html = FIXTURE_PATH.read_text()
        monkeypatch.setattr(
            "app.pipeline.stages.s1_spec_extraction.fetch_page_html",
            lambda url, proxy_country=None: html,
        )
        p1 = extract_specs(TEST_URL)
        p2 = extract_specs(TEST_URL)
        assert p1.id == p2.id

    def test_cleanup(self):
        with _db.SessionLocal() as session:
            session.query(SourceProduct).filter_by(url=TEST_URL).delete()
            session.commit()
