"""Captcha detection and resolution. Browserbase auto-solve is attempted first;
if it fails, the maintainer is emailed a live session link to solve manually.
The agent polls until the captcha clears or a 10-minute timeout expires."""

import logging
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page

from app.base.config import settings
from app.services.gmail import GmailService

log = logging.getLogger(__name__)

CAPTCHA_WIDGET_SELECTORS = [
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='recaptcha']",
    "iframe[src*='challenges.cloudflare.com']",
    "#captcha",
    ".captcha",
]

CAPTCHA_TEXT_MARKERS = [
    "security check is required",
    "verify you are human",
    "checking your browser",
]

AUTO_SOLVE_TIMEOUT = 30
MANUAL_SOLVE_POLL_INTERVAL = 10
MANUAL_SOLVE_TIMEOUT = 600


@dataclass
class CaptchaSolveState:
    detected: bool = False
    auto_solve_started: bool = False
    auto_solve_finished: bool = False


def _detect_captcha(page: Page) -> bool:
    for selector in CAPTCHA_WIDGET_SELECTORS:
        if page.locator(selector).count() > 0:
            return True
    content = page.content().lower()
    return any(marker in content for marker in CAPTCHA_TEXT_MARKERS)


def _attach_console_listener(page: Page, state: CaptchaSolveState) -> None:
    def on_console(msg):
        if msg.text == "browserbase-solving-started":
            log.info("Browserbase auto-solve started")
            state.auto_solve_started = True
        elif msg.text == "browserbase-solving-finished":
            log.info("Browserbase auto-solve finished")
            state.auto_solve_finished = True

    page.on("console", on_console)


def _wait_for_auto_solve(page: Page, state: CaptchaSolveState) -> bool:
    deadline = time.monotonic() + AUTO_SOLVE_TIMEOUT
    while time.monotonic() < deadline:
        if state.auto_solve_finished:
            page.wait_for_timeout(2000)
            if not _detect_captcha(page):
                return True
        if not _detect_captcha(page):
            return True
        time.sleep(2)
    return False


def _send_captcha_alert(
    subject: str, message: str, session_url: str
) -> None:
    body = (
        f"{message}\n\n"
        f"Solve the captcha here:\n{session_url}\n\n"
        f"The agent will resume automatically once the captcha is cleared."
    )
    gmail = GmailService()
    gmail.send_email(
        to=settings.MAINTAINER_EMAIL_ADDRESS,
        subject=f"[Captcha Alert] {subject}",
        body=body,
    )
    log.info("Captcha alert sent to %s", settings.MAINTAINER_EMAIL_ADDRESS)


def _wait_for_manual_solve(page: Page) -> bool:
    deadline = time.monotonic() + MANUAL_SOLVE_TIMEOUT
    while time.monotonic() < deadline:
        if not _detect_captcha(page):
            return True
        time.sleep(MANUAL_SOLVE_POLL_INTERVAL)
    return False


def handle_captcha(
    page: Page,
    session_url: str,
    subject: str,
    message: str,
) -> bool:
    """Detect and resolve a captcha, escalating to the maintainer if auto-solve fails.

    Returns True if the captcha was resolved, False if it timed out.
    """
    if not _detect_captcha(page):
        return True

    log.info("Captcha detected — waiting for Browserbase auto-solve")
    state = CaptchaSolveState(detected=True)
    _attach_console_listener(page, state)

    if _wait_for_auto_solve(page, state):
        log.info("Captcha resolved by auto-solve")
        return True

    log.warning("Auto-solve failed — escalating to maintainer")
    _send_captcha_alert(subject, message, session_url)

    if _wait_for_manual_solve(page):
        log.info("Captcha resolved by maintainer")
        return True

    log.error("Captcha not resolved within %ds", MANUAL_SOLVE_TIMEOUT)
    return False
