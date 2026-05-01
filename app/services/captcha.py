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
    "#nc_1_n1z",
]

CAPTCHA_TEXT_MARKERS = [
    "security check is required",
    "verify you are human",
    "checking your browser",
    "slide to verify",
    "detected unusual traffic",
]

AUTO_SOLVE_TIMEOUT = 30
MANUAL_SOLVE_POLL_INTERVAL = 10
MANUAL_SOLVE_TIMEOUT = 600


@dataclass
class CaptchaSolveState:
    detected: bool = False
    auto_solve_started: bool = False
    auto_solve_finished: bool = False


CAPTCHA_URL_MARKERS = [
    "_____tmd_____/punish",
    "action=captcha",
]


def _detect_captcha(page: Page) -> bool:
    url = page.url.lower()
    if any(marker in url for marker in CAPTCHA_URL_MARKERS):
        return True
    for selector in CAPTCHA_WIDGET_SELECTORS:
        if page.locator(selector).count() > 0:
            return True
    # Check visible text only — page.content() includes scripts which
    # contain captcha-related strings as i18n keys on non-captcha pages.
    visible_text = page.inner_text("body").lower()
    return any(marker in visible_text for marker in CAPTCHA_TEXT_MARKERS)


SLIDER_HANDLE = "#nc_1_n1z"
SLIDER_TRACK = ".nc_scale"
SLIDER_SOLVE_RETRIES = 3


def _try_slider_solve(page: Page) -> bool:
    """Drag the Alibaba NoCaptcha slider from left to right."""
    handle = page.locator(SLIDER_HANDLE)
    track = page.locator(SLIDER_TRACK)
    if handle.count() == 0 or track.count() == 0:
        return False

    for attempt in range(SLIDER_SOLVE_RETRIES):
        handle_box = handle.bounding_box()
        track_box = track.bounding_box()
        if not handle_box or not track_box:
            return False

        start_x = handle_box["x"] + handle_box["width"] / 2
        start_y = handle_box["y"] + handle_box["height"] / 2
        end_x = track_box["x"] + track_box["width"] - 10

        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(end_x, start_y, steps=30)
        page.mouse.up()
        page.wait_for_timeout(2000)

        if not _detect_captcha(page):
            log.info("Slider captcha solved on attempt %d", attempt + 1)
            return True
        log.info("Slider attempt %d did not clear captcha", attempt + 1)

    return False


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

    if page.locator(SLIDER_HANDLE).count() > 0 and page.locator(SLIDER_TRACK).count() > 0:
        log.info("Slider captcha detected — attempting drag solve")
        if _try_slider_solve(page):
            return True
        log.warning("Slider solve failed — falling through to auto-solve")

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
