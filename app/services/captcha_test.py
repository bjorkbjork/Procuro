"""Live test: captcha detection and escalation against hCaptcha demo page."""

import logging

import pytest

from app.services.browser import BrowserSession
from app.services.captcha import handle_captcha

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@pytest.mark.integration
def test_captcha_escalation():
    with BrowserSession(keep_alive=True) as s:
        s.page.goto("https://accounts.hcaptcha.com/demo", wait_until="domcontentloaded")

        resolved = handle_captcha(
            page=s.page,
            session_url=s.live_url,
            subject="Test: hCaptcha demo",
            message="Testing captcha escalation flow against hCaptcha demo page",
        )

        if resolved:
            print("Captcha resolved successfully")
        else:
            print("Captcha was not resolved within timeout")


if __name__ == "__main__":
    test_captcha_escalation()
