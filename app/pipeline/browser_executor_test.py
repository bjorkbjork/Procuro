"""Tests for BrowserFallbackExecutor and run_with_browser_fallback.

Uses a concrete test subclass with simple deterministic/fallback impls.
Mocks BrowserSession, authenticate_platform, and bb."""

from unittest.mock import MagicMock, patch

import pytest

from app.db import database as _db
from app.db.models.automation_event import AutomationEvent
from app.pipeline.browser_executor import (
    BrowserFallbackExecutor,
    FallbackResult,
    run_with_browser_fallback,
)


@pytest.fixture(autouse=True)
def clean_events():
    """Remove test automation events before and after each test."""
    with _db.SessionLocal() as session:
        session.query(AutomationEvent).filter(
            AutomationEvent.stage == "test_stage"
        ).delete()
        session.commit()
    yield
    with _db.SessionLocal() as session:
        session.query(AutomationEvent).filter(
            AutomationEvent.stage == "test_stage"
        ).delete()
        session.commit()


def _mock_browser():
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.session_id = "fake-session-123"
    mock.page = MagicMock()
    return mock


def _executor_patches():
    """Common patches for executor tests."""
    mock_browser = _mock_browser()
    mock_platform = MagicMock()
    mock_platform.platform.value = "alibaba"

    return (
        mock_browser,
        mock_platform,
        patch(
            "app.pipeline.browser_executor.BrowserSession",
            return_value=mock_browser,
        ),
        patch(
            "app.pipeline.browser_executor.authenticate_platform",
            return_value="fake-ctx",
        ),
        patch(
            "app.pipeline.browser_executor.get_platforms",
            return_value=[mock_platform],
        ),
        patch("app.pipeline.browser_executor.bb"),
    )


class StubExecutor(BrowserFallbackExecutor):
    """Concrete executor for testing with configurable behavior."""

    def __init__(self, items, det_fn=None, agent_fn=None, success_fn=None):
        self._items = items
        self._det_fn = det_fn or (lambda item, page, plat, ctx: "ok")
        self._agent_fn = agent_fn or (
            lambda item, sid, plat: FallbackResult(success=False)
        )
        self._success_fn = success_fn or (lambda item, result: None)
        self.successes = []

    @property
    def stage(self):
        return "test_stage"

    @property
    def action(self):
        return "test_action"

    def get_work_items(self):
        return {"alibaba": self._items}

    def deterministic_action(self, item, page, platform, context_id):
        return self._det_fn(item, page, platform, context_id)

    def agent_fallback(self, item, session_id, platform):
        return self._agent_fn(item, session_id, platform)

    def on_success(self, item, result):
        self.successes.append((item, result))
        if self._success_fn:
            self._success_fn(item, result)

    def thread_label(self, item):
        return f"test-{item}"

    def get_thread_id(self, item):
        return None


class TestBrowserFallbackExecutor:
    def test_deterministic_success_records_event(self):
        mock_browser, mock_platform, *patches = _executor_patches()

        executor = StubExecutor(["item1"])

        with patches[0], patches[1], patches[2], patches[3]:
            results = executor.execute()

        assert len(results) == 1
        assert results[0] == ("item1", "ok")
        assert executor.successes == [("item1", "ok")]

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "deterministic"
            assert events[0].action == "test_action"

    def test_agent_fallback_records_event(self):
        mock_browser, mock_platform, *patches = _executor_patches()

        def det_fail(item, page, plat, ctx):
            raise RuntimeError("det failed")

        def agent_ok(item, sid, plat):
            return FallbackResult(success=True, result="recovered")

        executor = StubExecutor(["item1"], det_fn=det_fail, agent_fn=agent_ok)

        with patches[0], patches[1], patches[2], patches[3]:
            results = executor.execute()

        assert len(results) == 1
        assert results[0] == ("item1", "recovered")
        assert executor.successes == [("item1", "recovered")]

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "agent_fallback"
            assert "det failed" in events[0].detail

    def test_both_fail_records_failed_event(self):
        mock_browser, mock_platform, *patches = _executor_patches()

        def det_fail(item, page, plat, ctx):
            raise RuntimeError("det failed")

        def agent_fail(item, sid, plat):
            raise RuntimeError("agent failed")

        executor = StubExecutor(["item1"], det_fn=det_fail, agent_fn=agent_fail)

        with patches[0], patches[1], patches[2], patches[3]:
            results = executor.execute()

        assert len(results) == 1
        assert results[0] == ("item1", None)
        assert executor.successes == []

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "failed"
            assert "det failed" in events[0].detail
            assert "agent failed" in events[0].detail

    def test_skip_records_no_event(self):
        mock_browser, mock_platform, *patches = _executor_patches()

        def det_skip(item, page, plat, ctx):
            return None

        executor = StubExecutor(["item1"], det_fn=det_skip)

        with patches[0], patches[1], patches[2], patches[3]:
            results = executor.execute()

        assert len(results) == 1
        assert results[0] == ("item1", None)

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 0

    def test_empty_work_items(self):
        executor = StubExecutor([])
        executor._items = []
        executor.get_work_items = lambda: {}
        results = executor.execute()
        assert results == []

    def test_login_required_triggers_reauth(self):
        mock_browser, mock_platform, *patches = _executor_patches()

        call_count = {"det": 0}

        def det_fail(item, page, plat, ctx):
            call_count["det"] += 1
            raise RuntimeError("det failed")

        def agent_login_required(item, sid, plat):
            return FallbackResult(success=False, login_required=True)

        executor = StubExecutor(
            ["item1"], det_fn=det_fail, agent_fn=agent_login_required
        )

        with patches[0], patches[1], patches[2], patches[3]:
            results = executor.execute()

        # 1 pool attempt + up to REAUTH_MAX_RETRIES (5) re-auth retries
        from app.base.config import settings

        assert call_count["det"] == 1 + settings.REAUTH_MAX_RETRIES


class TestRunWithBrowserFallback:
    def test_deterministic_success(self):
        mock_browser = _mock_browser()

        with (
            patch(
                "app.pipeline.browser_executor.BrowserSession",
                return_value=mock_browser,
            ),
            patch("app.pipeline.browser_executor.bb"),
        ):
            result = run_with_browser_fallback(
                "fake-ctx",
                lambda page: "success",
                lambda sid: FallbackResult(success=False),
                stage="test_stage",
                action="test_action",
            )

        assert result == "success"

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "deterministic"

    def test_fallback_success(self):
        mock_browser = _mock_browser()

        def det_fail(page):
            raise RuntimeError("det failed")

        with (
            patch(
                "app.pipeline.browser_executor.BrowserSession",
                return_value=mock_browser,
            ),
            patch("app.pipeline.browser_executor.bb"),
        ):
            result = run_with_browser_fallback(
                "fake-ctx",
                det_fail,
                lambda sid: FallbackResult(success=True, result="recovered"),
                stage="test_stage",
                action="test_action",
            )

        assert result == "recovered"

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "agent_fallback"

    def test_both_fail(self):
        mock_browser = _mock_browser()

        with (
            patch(
                "app.pipeline.browser_executor.BrowserSession",
                return_value=mock_browser,
            ),
            patch("app.pipeline.browser_executor.bb"),
        ):
            result = run_with_browser_fallback(
                "fake-ctx",
                lambda page: (_ for _ in ()).throw(RuntimeError("det")),
                lambda sid: (_ for _ in ()).throw(RuntimeError("agent")),
                stage="test_stage",
                action="test_action",
            )

        assert result is None

        with _db.SessionLocal() as session:
            events = session.query(AutomationEvent).filter_by(stage="test_stage").all()
            assert len(events) == 1
            assert events[0].outcome == "failed"
