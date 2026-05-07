"""Browser-fallback executor — per-batch auth, deterministic → agent fallback.

Builds on BatchExecutor (batch_executor.py) adding per-batch platform
authentication, per-item BrowserSession lifecycle, deterministic → agent
fallback, re-auth on login_required, and AutomationEvent recording.

Subclasses define only:
  - what work items to process (grouped by platform)
  - what the deterministic Playwright action is
  - what the LLM agent fallback is
  - how to handle success
"""

import logging
import threading
from abc import abstractmethod
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from sqlalchemy import func as sa_func

from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.automation_event import AutomationEvent
from app.pipeline.batch_executor import BatchExecutor
from app.services.browser import BrowserSession, authenticate_platform, bb
from app.services.platforms import get_platforms

log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


def record_automation_event(
    stage: str,
    action: str,
    outcome: str,
    supplier_thread_id: int | None = None,
    detail: str | None = None,
) -> None:
    """Write one AutomationEvent row. Called internally by the executor."""
    try:
        with SessionLocal() as session:
            session.add(
                AutomationEvent(
                    stage=stage,
                    action=action,
                    outcome=outcome,
                    supplier_thread_id=supplier_thread_id,
                    detail=detail,
                )
            )
            session.commit()
    except Exception:
        log.exception("Failed to record automation event")


@dataclass
class FallbackResult:
    success: bool
    login_required: bool = False
    result: object = None


@dataclass
class _AttemptResult:
    item: object
    result: object
    login_required: bool = False

    def as_tuple(self) -> tuple:
        return (self.item, self.result)


class BrowserFallbackExecutor(BatchExecutor[T, R]):
    """Browser-based executor with per-batch authentication.

    Each worker thread authenticates independently (own context_id) so
    no two concurrent sessions share the same auth cookies / proxy IP.
    """

    @abstractmethod
    def deterministic_action(
        self, item: T, page, platform, context_id: str
    ) -> R | None:
        """Run the deterministic Playwright action for one work item.

        Returns a result on success. Return None to skip (no fallback).
        Raise any exception to trigger agent fallback.
        """
        ...

    @abstractmethod
    def agent_fallback(self, item: T, session_id: str, platform) -> FallbackResult:
        """Run the LLM agent recovery for a failed deterministic action."""
        ...

    @abstractmethod
    def on_success(self, item: T, result: R) -> None:
        """Handle a successful action (record to DB, update state, etc.)."""
        ...

    def get_thread_id(self, item: T) -> int | None:
        """Return the supplier_thread_id for AutomationEvent FK. Default None."""
        return None

    def on_skip(self, item: T) -> None:
        """Called when deterministic_action returns None (skip). Default no-op."""
        pass

    def _resolve_platforms(self) -> dict[str, object]:
        """Build a platform_name → SupplierPlatform mapping."""
        return {p.platform.value: p for p in get_platforms()}

    def _attempt_one(self, item: T, context_id: str, platform) -> _AttemptResult:
        """Process one item: BrowserSession → deterministic → agent fallback."""
        threading.current_thread().name = self.thread_label(item)

        browser = BrowserSession(
            proxy_country="AU",
            proxy_city="SYDNEY",
            context_id=context_id,
            keep_alive=True,
        )
        browser.__enter__()
        session_id = browser.session_id

        try:
            result = self.deterministic_action(item, browser.page, platform, context_id)
        except Exception as det_exc:
            log.exception("Deterministic action failed for %s", self.thread_label(item))
            browser.detach()

            try:
                fb = self.agent_fallback(item, session_id, platform)
            except Exception as agent_exc:
                log.exception("Agent fallback error for %s", self.thread_label(item))
                record_automation_event(
                    self.stage,
                    self.action,
                    "failed",
                    self.get_thread_id(item),
                    f"deterministic: {det_exc} | agent: {agent_exc}",
                )
                bb.sessions.update(session_id, status="REQUEST_RELEASE")
                return _AttemptResult(item=item, result=None)

            bb.sessions.update(session_id, status="REQUEST_RELEASE")

            if fb.login_required:
                record_automation_event(
                    self.stage,
                    self.action,
                    "failed",
                    self.get_thread_id(item),
                    f"login_required: {det_exc}",
                )
                return _AttemptResult(item=item, result=None, login_required=True)

            if fb.success:
                record_automation_event(
                    self.stage,
                    self.action,
                    "agent_fallback",
                    self.get_thread_id(item),
                    str(det_exc),
                )
                self.on_success(item, fb.result)
                return _AttemptResult(item=item, result=fb.result)

            record_automation_event(
                self.stage,
                self.action,
                "failed",
                self.get_thread_id(item),
                str(det_exc),
            )
            return _AttemptResult(item=item, result=None)

        else:
            if result is None:
                browser.__exit__(None, None, None)
                self.on_skip(item)
                return _AttemptResult(item=item, result=None)

            browser.__exit__(None, None, None)
            record_automation_event(
                self.stage,
                self.action,
                "deterministic",
                self.get_thread_id(item),
            )
            self.on_success(item, result)
            return _AttemptResult(item=item, result=result)

    def _process_batch(
        self, batch: list[T], group_key: str
    ) -> list[tuple[T, R | None]]:
        """Authenticate, process items sequentially, re-auth on login failures."""
        platform_objs = self._resolve_platforms()
        platform = platform_objs.get(group_key)
        if not platform:
            log.warning(
                "No platform for '%s' — skipping %d items", group_key, len(batch)
            )
            return [(item, None) for item in batch]

        try:
            context_id = authenticate_platform(platform)
        except Exception:
            log.exception(
                "Could not authenticate on %s — skipping %d items",
                group_key,
                len(batch),
            )
            return [(item, None) for item in batch]

        results: list[tuple[T, R | None]] = []
        reauth_items: list[T] = []

        for item in batch:
            ar = self._attempt_one(item, context_id, platform)
            results.append(ar.as_tuple())
            if ar.login_required:
                reauth_items.append(item)

        # Per-batch re-auth retry loop
        for attempt in range(settings.REAUTH_MAX_RETRIES):
            if not reauth_items:
                break

            log.info(
                "Re-authenticating on %s after login prompt (attempt %d/%d)",
                group_key,
                attempt + 1,
                settings.REAUTH_MAX_RETRIES,
            )
            try:
                context_id = authenticate_platform(platform)
            except Exception:
                log.exception("Re-auth failed on %s", group_key)
                break

            retry_batch = list(reauth_items)
            reauth_items.clear()

            for item in retry_batch:
                ar = self._attempt_one(item, context_id, platform)
                results.append(ar.as_tuple())
                if ar.login_required:
                    reauth_items.append(item)

        return results


def run_with_browser_fallback(
    context_id: str,
    deterministic_fn: Callable,
    fallback_fn: Callable[[str], FallbackResult],
    *,
    stage: str,
    action: str,
    supplier_thread_id: int | None = None,
) -> object | None:
    """Execute a single browser action with deterministic-then-agent fallback.

    For one-shot use where the full executor ABC is overkill (e.g. stage 5
    reply_fn). Same browser lifecycle and event recording as the executor.
    """
    browser = BrowserSession(
        proxy_country="AU",
        proxy_city="SYDNEY",
        context_id=context_id,
        keep_alive=True,
    )
    browser.__enter__()
    session_id = browser.session_id

    try:
        result = deterministic_fn(browser.page)
    except Exception as det_exc:
        log.exception("Deterministic action failed, trying agent fallback")
        browser.detach()
        try:
            fb = fallback_fn(session_id)
        except Exception as agent_exc:
            log.exception("Agent fallback also failed")
            record_automation_event(
                stage,
                action,
                "failed",
                supplier_thread_id,
                f"deterministic: {det_exc} | agent: {agent_exc}",
            )
            bb.sessions.update(session_id, status="REQUEST_RELEASE")
            return None

        bb.sessions.update(session_id, status="REQUEST_RELEASE")

        if fb.success:
            record_automation_event(
                stage,
                action,
                "agent_fallback",
                supplier_thread_id,
                str(det_exc),
            )
            return fb.result

        record_automation_event(
            stage,
            action,
            "failed",
            supplier_thread_id,
            str(det_exc),
        )
        return None
    else:
        browser.__exit__(None, None, None)
        record_automation_event(
            stage,
            action,
            "deterministic",
            supplier_thread_id,
        )
        return result


def check_automation_failure_rate() -> None:
    """Email maintainer if any (stage, action) pair exceeds the failure threshold.

    Checks the last AUTOMATION_FAILURE_ALERT_WINDOW events per pair. Follows
    the same alert pattern as s2_supplier_search._alert_low_matches.
    """
    maintainer = settings.MAINTAINER_EMAIL_ADDRESS
    if not maintainer:
        return

    window = settings.AUTOMATION_FAILURE_ALERT_WINDOW
    threshold = settings.AUTOMATION_FAILURE_ALERT_THRESHOLD

    with SessionLocal() as session:
        pairs = (
            session.query(AutomationEvent.stage, AutomationEvent.action)
            .distinct()
            .all()
        )

        alerts = []
        for stage, action in pairs:
            recent = (
                session.query(AutomationEvent)
                .filter_by(stage=stage, action=action)
                .order_by(AutomationEvent.created_at.desc())
                .limit(window)
                .all()
            )
            if not recent:
                continue

            total = len(recent)
            failed = sum(1 for e in recent if e.outcome == "failed")
            rate = failed / total

            if rate > threshold:
                alerts.append(
                    f"  {stage}/{action}: {failed}/{total} failed "
                    f"({rate:.0%} > {threshold:.0%} threshold)"
                )

    if not alerts:
        return

    try:
        from app.services.gmail import GmailService

        gmail = GmailService()
        gmail.send_email(
            to=maintainer,
            subject="[Automation Alert] High failure rate detected",
            body=(
                "The following automation pairs exceeded the failure threshold "
                f"(last {window} events):\n\n"
                + "\n".join(alerts)
                + "\n\nCheck the Automation Stats tab in the output sheet for details."
            ),
        )
    except Exception:
        log.exception("Failed to send automation failure alert")
