"""Stage 2 tests.

Unit tests mock the DB and LLM calls. The integration test at the bottom hits
real Alibaba + Browserbase and is excluded from default pytest runs.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct
from app.pipeline.stages.s2_supplier_search import (
    run_supplier_search,
    search_and_extract,
)


class TestRunSupplierSearch:
    def _mock_session_factory(self, thread_counts, candidate_counts):
        """Return a SessionLocal replacement that yields controlled counts.

        thread_counts / candidate_counts are lists consumed in order across
        loop iterations. Each iteration makes two query() calls (threads then
        candidates). A final call after the loop uses .all() to return threads.
        """
        call_idx = {"n": 0}

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, model, pk):
                m = MagicMock()
                m.title = "Test Product"
                return m

            def query(self, entity):
                q = MagicMock()
                idx = call_idx["n"]
                call_idx["n"] += 1
                tc_idx = min(idx // 2, len(thread_counts) - 1)
                cc_idx = min(idx // 2, len(candidate_counts) - 1)
                if idx % 2 == 0:
                    q.filter_by.return_value.count.return_value = thread_counts[tc_idx]
                    q.filter_by.return_value.all.return_value = [
                        MagicMock() for _ in range(thread_counts[tc_idx])
                    ]
                else:
                    q.filter_by.return_value.count.return_value = candidate_counts[
                        cc_idx
                    ]
                return q

        return FakeSession

    def test_stops_at_min_match_threshold(self):
        mock_search = MagicMock()
        mock_match = MagicMock()

        with (
            patch(
                "app.pipeline.stages.s2_supplier_search.search_and_extract",
                mock_search,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.match_candidates",
                mock_match,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search._alert_low_matches",
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.SessionLocal",
                self._mock_session_factory(
                    thread_counts=[settings.MIN_MATCHES_PER_PRODUCT],
                    candidate_counts=[50],
                ),
            ),
        ):
            result = run_supplier_search(1)

        assert mock_search.call_count == 1
        assert mock_match.call_count == 1
        mock_match.assert_called_with(1, only_pending=True)

    def test_retries_when_under_threshold(self):
        mock_search = MagicMock()
        mock_match = MagicMock()

        with (
            patch(
                "app.pipeline.stages.s2_supplier_search.search_and_extract",
                mock_search,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.match_candidates",
                mock_match,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search._alert_low_matches",
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.SessionLocal",
                self._mock_session_factory(
                    thread_counts=[3, 7, settings.MIN_MATCHES_PER_PRODUCT],
                    candidate_counts=[20, 40, 60],
                ),
            ),
        ):
            run_supplier_search(1)

        assert mock_search.call_count == 3
        assert mock_match.call_count == 3

    def test_gives_up_at_candidate_limit(self):
        mock_search = MagicMock()
        mock_match = MagicMock()

        with (
            patch(
                "app.pipeline.stages.s2_supplier_search.search_and_extract",
                mock_search,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.match_candidates",
                mock_match,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search._alert_low_matches",
            ) as mock_alert,
            patch(
                "app.pipeline.stages.s2_supplier_search.SessionLocal",
                self._mock_session_factory(
                    thread_counts=[2],
                    candidate_counts=[settings.MAX_CANDIDATES_PER_PRODUCT],
                ),
            ),
        ):
            result = run_supplier_search(1)

        assert mock_search.call_count == 1
        assert len(result) == 2
        mock_alert.assert_called_once()


SOURCE_URL = "https://www.kogan.com/au/buy/test-stage-two/"


@pytest.mark.integration
def test_search_and_extract(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.stages.s2_supplier_search.generate_search_queries",
        lambda title, specs: ["75 inch QLED 4K television"],
    )

    with SessionLocal() as session:
        source = SourceProduct(
            url=SOURCE_URL,
            slug="test-stage-two",
            title="75 inch QLED 4K Smart TV",
            specs={"Display": {"Screen Type": "QLED", "Screen Size": '75"'}},
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
            persisted = (
                session.query(SupplierProduct)
                .filter_by(
                    source_product_id=source_id,
                )
                .all()
            )
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
