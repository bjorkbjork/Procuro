"""Live test: run search_and_extract against real Alibaba + Browserbase.
Query agent is monkeypatched to skip the LLM call — everything else is real."""

import pytest

from app.agent.stage_two_supplier_search import search_and_extract
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct

SOURCE_URL = "https://www.kogan.com/au/buy/test-stage-two/"


@pytest.mark.integration
def test_search_and_extract(monkeypatch):
    monkeypatch.setattr(
        "app.agent.stage_two_supplier_search.generate_search_queries",
        lambda title, specs: ["75 inch QLED 4K television"],
    )

    with SessionLocal() as session:
        source = SourceProduct(
            url=SOURCE_URL,
            slug="test-stage-two",
            title="75 inch QLED 4K Smart TV",
            specs={"Display": {"Screen Type": "QLED", "Screen Size": "75\""}},
        )
        session.add(source)
        session.commit()
        source_id = source.id

    try:
        results = search_and_extract(source_id)
        assert len(results) > 0, "Expected at least one supplier product"

        for sp in results:
            print(f"\n{sp.title}")
            print(f"  URL: {sp.product_url}")
            print(f"  Price: {sp.price}  MOQ: {sp.moq}")
            print(f"  Spec groups: {list(sp.specs.keys()) if sp.specs else 'none'}")

        with SessionLocal() as session:
            persisted = session.query(SupplierProduct).filter_by(
                source_product_id=source_id,
            ).all()
            assert len(persisted) == len(results)
            for sp in persisted:
                assert sp.specs, f"No specs for {sp.product_url}"
                assert sp.title
    finally:
        with SessionLocal() as session:
            session.query(SupplierProduct).filter_by(
                source_product_id=source_id,
            ).delete()
            session.query(SourceProduct).filter_by(id=source_id).delete()
            session.commit()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
