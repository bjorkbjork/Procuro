"""Concurrent browser executor with deterministic-then-agent fallback.

Extracts the ThreadPoolExecutor + BrowserSession + LLM-agent-recovery
pattern used across pipeline stages. Subclasses define only:
  - what work items to process (grouped by platform)
  - what the deterministic Playwright action is
  - what the LLM agent fallback is
  - how to handle success

The executor handles: thread pooling, browser session lifecycle,
exception → detach → agent fallback, result collection, re-auth
signaling, and AutomationEvent recording."""

import logging
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from sqlalchemy import func as sa_func

from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.automation_event import AutomationEvent
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


class BrowserFallbackExecutor(ABC, Generic[T, R]):
    """Abstract concurrent executor for browser-based pipeline stages.

    Subclasses implement the abstract methods below. The executor handles
    ThreadPoolExecutor, BrowserSession lifecycle, detach-on-failure,
    result collection, re-auth, and AutomationEvent recording.
    """

    @property
    @abstractmethod
    def stage(self) -> str:
        """Pipeline stage identifier (e.g. 's3_outreach')."""
        ...

    @property
    @abstractmethod
    def action(self) -> str:
        """Action identifier (e.g. 'send_inquiry')."""
        ...

    @abstractmethod
    def get_work_items(self) -> dict[str, list[T]]:
        """Return work items grouped by platform name.

        Each platform group gets a single authenticate_platform() call
        and all items within share the resulting context_id.
        """
        ...

    @abstractmethod
    def deterministic_action(
        self, item: T, page, platform, context_id: str
    ) -> R | None:
        """Run the deterministic Playwright action for one work item.

        Args:
            item: The work item payload.
            page: A live Playwright Page from the BrowserSession.
            platform: The SupplierPlatform instance for this item's platform.
            context_id: The authenticated context for this platform.

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

    @abstractmethod
    def thread_label(self, item: T) -> str:
        """Return a label for the worker thread name."""
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

    def execute(self) -> list[tuple[T, R | None]]:
        """Run all work items concurrently with fallback. Returns results."""
        grouped = self.get_work_items()
        if not grouped:
            return []

        platform_objs = self._resolve_platforms()
        all_results: list[tuple[T, R | None]] = []

        for platform_name, items in grouped.items():
            platform = platform_objs.get(platform_name)
            if not platform:
                log.warning(
                    "No platform for '%s' — skipping %d items",
                    platform_name,
                    len(items),
                )
                continue

            log.info(
                "Processing %d items on %s [%s/%s]",
                len(items),
                platform_name,
                self.stage,
                self.action,
            )

            try:
                context_id = authenticate_platform(platform)
            except Exception:
                log.exception(
                    "Could not authenticate on %s — skipping %d items",
                    platform_name,
                    len(items),
                )
                continue

            reauth_needed = threading.Event()
            reauth_items: list[T] = []

            def _attempt(item: T, ctx_id: str = context_id) -> tuple[T, R | None]:
                threading.current_thread().name = self.thread_label(item)

                browser = BrowserSession(
                    proxy_country="AU",
                    context_id=ctx_id,
                    keep_alive=True,
                )
                browser.__enter__()
                session_id = browser.session_id

                try:
                    result = self.deterministic_action(
                        item, browser.page, platform, ctx_id
                    )
                except Exception as det_exc:
                    log.exception(
                        "Deterministic action failed for %s", self.thread_label(item)
                    )
                    browser.detach()

                    try:
                        fb = self.agent_fallback(item, session_id, platform)
                    except Exception as agent_exc:
                        log.exception(
                            "Agent fallback error for %s", self.thread_label(item)
                        )
                        record_automation_event(
                            self.stage,
                            self.action,
                            "failed",
                            self.get_thread_id(item),
                            f"deterministic: {det_exc} | agent: {agent_exc}",
                        )
                        bb.sessions.update(session_id, status="REQUEST_RELEASE")
                        return (item, None)

                    bb.sessions.update(session_id, status="REQUEST_RELEASE")

                    if fb.login_required:
                        reauth_needed.set()
                        reauth_items.append(item)
                        record_automation_event(
                            self.stage,
                            self.action,
                            "failed",
                            self.get_thread_id(item),
                            f"login_required: {det_exc}",
                        )
                        return (item, None)

                    if fb.success:
                        record_automation_event(
                            self.stage,
                            self.action,
                            "agent_fallback",
                            self.get_thread_id(item),
                            str(det_exc),
                        )
                        self.on_success(item, fb.result)
                        return (item, fb.result)

                    record_automation_event(
                        self.stage,
                        self.action,
                        "failed",
                        self.get_thread_id(item),
                        str(det_exc),
                    )
                    return (item, None)

                else:
                    if result is None:
                        browser.__exit__(None, None, None)
                        self.on_skip(item)
                        return (item, None)

                    browser.__exit__(None, None, None)
                    record_automation_event(
                        self.stage,
                        self.action,
                        "deterministic",
                        self.get_thread_id(item),
                    )
                    self.on_success(item, result)
                    return (item, result)

            with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
                futures = {pool.submit(_attempt, item): item for item in items}
                for future in as_completed(futures):
                    try:
                        all_results.append(future.result())
                    except Exception:
                        item = futures[future]
                        log.exception("Unhandled error for %s", self.thread_label(item))
                        all_results.append((item, None))

            # Re-auth retry loop with cap — items that hit LOGIN_REQUIRED
            # get retried after re-authentication, up to REAUTH_MAX_RETRIES
            for attempt in range(settings.REAUTH_MAX_RETRIES):
                if not reauth_needed.is_set() or not reauth_items:
                    break

                log.info(
                    "Re-authenticating on %s after login prompt (attempt %d/%d)",
                    platform_name,
                    attempt + 1,
                    settings.REAUTH_MAX_RETRIES,
                )
                try:
                    context_id = authenticate_platform(platform)
                except Exception:
                    log.exception("Re-auth failed on %s", platform_name)
                    break

                retry_batch = list(reauth_items)
                reauth_items.clear()
                reauth_needed.clear()

                for item in retry_batch:
                    try:
                        all_results.append(_attempt(item, context_id))
                    except Exception:
                        log.exception(
                            "Re-auth retry failed for %s", self.thread_label(item)
                        )
                        all_results.append((item, None))

        return all_results


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
