"""Tests for RotatingModel: RPM tracking, 429 rotation, window expiry."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters

from app.base.llm import RotatingModel


def _fake_response(model_name: str = "test") -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=f"from {model_name}")])


def _make_pool(specs: list[tuple[str, int]]) -> RotatingModel:
    """Build a RotatingModel with mocked inner models (no real Bedrock)."""
    with (
        patch("app.base.llm.get_single_model") as mock_gsm,
        patch("app.base.llm.bedrock_provider") as mock_bp,
    ):
        mock_bp.return_value = MagicMock()

        def make_mock_model(model_id):
            m = MagicMock()
            m.model_name = model_id
            m.request = AsyncMock(return_value=_fake_response(model_id))
            return m

        mock_gsm.side_effect = lambda mid: make_mock_model(mid)
        return RotatingModel(specs)


def _params() -> ModelRequestParameters:
    return ModelRequestParameters()


class TestPickModel:
    def test_picks_first_model_when_all_idle(self):
        rm = _make_pool([("model-a", 5), ("model-b", 10)])
        picked = rm._pick_model()
        assert picked.model_name == "model-a"

    def test_rotates_when_first_exhausted(self):
        rm = _make_pool([("model-a", 2), ("model-b", 10)])
        rm._pick_model()
        rm._pick_model()
        third = rm._pick_model()
        assert third.model_name == "model-b"

    def test_respects_rpm_limits(self):
        rm = _make_pool([("model-a", 3), ("model-b", 2)])
        names = [rm._pick_model().model_name for _ in range(5)]
        assert names == ["model-a", "model-a", "model-a", "model-b", "model-b"]

    def test_all_exhausted_waits_then_returns(self):
        rm = _make_pool([("model-a", 1), ("model-b", 1)])
        rm._pick_model()
        rm._pick_model()

        # Backdate the oldest timestamp so it expires immediately
        now = time.monotonic()
        rm._timestamps["model-a"] = [now - 61]
        picked = rm._pick_model()
        assert picked.model_name == "model-a"


class TestRpmWindowExpiry:
    def test_timestamps_expire_after_60_seconds(self):
        rm = _make_pool([("model-a", 2)])
        rm._pick_model()
        rm._pick_model()

        # Both slots used — backdate them past the 60s window
        now = time.monotonic()
        rm._timestamps["model-a"] = [now - 61, now - 61]

        picked = rm._pick_model()
        assert picked.model_name == "model-a"
        assert (
            len([t for t in rm._timestamps["model-a"] if time.monotonic() - t < 60])
            == 1
        )

    def test_partial_expiry_frees_one_slot(self):
        rm = _make_pool([("model-a", 2)])
        rm._pick_model()
        rm._pick_model()

        now = time.monotonic()
        # One old, one fresh — should free exactly one slot
        rm._timestamps["model-a"] = [now - 61, now]

        picked = rm._pick_model()
        assert picked.model_name == "model-a"
        fresh = [t for t in rm._timestamps["model-a"] if time.monotonic() - t < 60]
        assert len(fresh) == 2

    def test_full_window_cycles(self):
        """Simulate a full minute of usage, then verify the window resets."""
        rm = _make_pool([("model-a", 3)])
        rm._pick_model()
        rm._pick_model()
        rm._pick_model()

        # All 3 slots used
        assert len(rm._timestamps["model-a"]) == 3

        # Expire all
        now = time.monotonic()
        rm._timestamps["model-a"] = [now - 65, now - 63, now - 61]

        # Should be able to pick 3 more
        for _ in range(3):
            picked = rm._pick_model()
            assert picked.model_name == "model-a"


class TestMarkExhausted:
    def test_fills_rpm_window(self):
        rm = _make_pool([("model-a", 5), ("model-b", 3)])
        model_a = rm._pool[0][0]
        rm._mark_exhausted(model_a)
        assert len(rm._timestamps["model-a"]) == 5

        # Next pick must skip model-a
        picked = rm._pick_model()
        assert picked.model_name == "model-b"

    def test_exhausted_model_recovers_after_window(self):
        rm = _make_pool([("model-a", 2)])
        model_a = rm._pool[0][0]
        rm._mark_exhausted(model_a)

        # Backdate all timestamps past the window
        now = time.monotonic()
        rm._timestamps["model-a"] = [now - 61] * 2

        picked = rm._pick_model()
        assert picked.model_name == "model-a"


class TestRequestRotation:
    def test_successful_request_uses_first_model(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10)])
        response = asyncio.get_event_loop().run_until_complete(
            rm.request([], None, _params())
        )
        assert "model-a" in response.parts[0].content
        rm._pool[0][0].request.assert_awaited_once()
        rm._pool[1][0].request.assert_not_awaited()

    def test_429_rotates_to_next_model(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10)])
        rm._pool[0][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-a", "throttled")
        )
        response = asyncio.get_event_loop().run_until_complete(
            rm.request([], None, _params())
        )
        assert "model-b" in response.parts[0].content
        rm._pool[0][0].request.assert_awaited_once()
        rm._pool[1][0].request.assert_awaited_once()

    def test_429_marks_model_exhausted(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10)])
        rm._pool[0][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-a", "throttled")
        )
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        # model-a should be fully exhausted
        assert len(rm._timestamps["model-a"]) == 10

    def test_non_429_error_propagates(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10)])
        rm._pool[0][0].request = AsyncMock(
            side_effect=ModelHTTPError(500, "model-a", "server error")
        )
        with pytest.raises(ModelHTTPError) as exc_info:
            asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        assert exc_info.value.status_code == 500
        # Should NOT have tried model-b
        rm._pool[1][0].request.assert_not_awaited()

    def test_all_models_429_waits_and_retries(self):
        rm = _make_pool([("model-a", 1), ("model-b", 1)])
        call_count = {"a": 0}

        async def a_fail_then_succeed(*args, **kwargs):
            call_count["a"] += 1
            if call_count["a"] <= 1:
                raise ModelHTTPError(429, "model-a", "throttled")
            return _fake_response("model-a")

        rm._pool[0][0].request = AsyncMock(side_effect=a_fail_then_succeed)
        rm._pool[1][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-b", "throttled")
        )

        def _expire_all(_seconds):
            """Instead of sleeping, backdate timestamps so capacity frees up."""
            now = time.monotonic()
            for name in rm._timestamps:
                rm._timestamps[name] = [now - 61 for _ in rm._timestamps[name]]

        with patch("app.base.llm.time.sleep", side_effect=_expire_all):
            response = asyncio.get_event_loop().run_until_complete(
                rm.request([], None, _params())
            )
        assert "model-a" in response.parts[0].content

    def test_cascading_429_through_three_models(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10), ("model-c", 10)])
        rm._pool[0][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-a", "throttled")
        )
        rm._pool[1][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-b", "throttled")
        )
        response = asyncio.get_event_loop().run_until_complete(
            rm.request([], None, _params())
        )
        assert "model-c" in response.parts[0].content
        rm._pool[0][0].request.assert_awaited_once()
        rm._pool[1][0].request.assert_awaited_once()
        rm._pool[2][0].request.assert_awaited_once()


class TestUsageCarriesAcrossRequests:
    """RPM state from previous requests affects model selection for later ones."""

    def test_prior_usage_shifts_to_next_model(self):
        rm = _make_pool([("model-a", 2), ("model-b", 10)])
        # First two requests use model-a
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        # Third request should go to model-b (model-a at limit)
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))

        assert rm._pool[0][0].request.await_count == 2
        assert rm._pool[1][0].request.await_count == 1

    def test_429_exhaustion_persists_across_requests(self):
        rm = _make_pool([("model-a", 10), ("model-b", 10)])
        rm._pool[0][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-a", "throttled")
        )
        # First request: 429 on model-a, falls to model-b
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        assert len(rm._timestamps["model-a"]) == 10

        # Second request: model-a still exhausted, goes straight to model-b
        rm._pool[0][0].request.reset_mock()
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        # model-a should not have been tried because _pick_model skips it
        rm._pool[0][0].request.assert_not_awaited()

    def test_mixed_usage_and_429_tracking(self):
        rm = _make_pool([("model-a", 3), ("model-b", 5), ("model-c", 10)])
        # Use 2 of model-a's 3 slots via normal requests
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))

        # Third request uses model-a's last slot
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        assert rm._pool[0][0].request.await_count == 3

        # Fourth request goes to model-b
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        assert rm._pool[1][0].request.await_count == 1

        # Now 429 model-b to exhaust it
        rm._pool[1][0].request = AsyncMock(
            side_effect=ModelHTTPError(429, "model-b", "throttled")
        )
        asyncio.get_event_loop().run_until_complete(rm.request([], None, _params()))
        # Should have fallen through to model-c
        assert rm._pool[2][0].request.await_count == 1


class TestSingleModelPool:
    def test_single_model_works(self):
        rm = _make_pool([("only-model", 10)])
        response = asyncio.get_event_loop().run_until_complete(
            rm.request([], None, _params())
        )
        assert "only-model" in response.parts[0].content

    def test_single_model_429_waits_and_retries(self):
        rm = _make_pool([("only-model", 1)])
        call_count = {"n": 0}

        async def fail_then_succeed(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ModelHTTPError(429, "only-model", "throttled")
            return _fake_response("only-model")

        rm._pool[0][0].request = AsyncMock(side_effect=fail_then_succeed)

        def _expire_all(_seconds):
            now = time.monotonic()
            for name in rm._timestamps:
                rm._timestamps[name] = [now - 61 for _ in rm._timestamps[name]]

        with patch("app.base.llm.time.sleep", side_effect=_expire_all):
            response = asyncio.get_event_loop().run_until_complete(
                rm.request([], None, _params())
            )
        assert "only-model" in response.parts[0].content
        assert call_count["n"] == 2
