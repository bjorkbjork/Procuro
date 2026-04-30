"""
Integration tests for Browserbase service.
Runs against real Browserbase sessions.
"""

import pytest

from app.services.browser import BrowserSession


class TestBrowserSession:
    def test_session_opens_and_navigates(self):
        with BrowserSession() as s:
            s.page.goto("https://www.example.com")
            assert "Example Domain" in s.page.title()

    def test_page_content_accessible(self):
        with BrowserSession() as s:
            s.page.goto("https://www.example.com")
            body = s.page.text_content("body")
            assert "documentation examples" in body.lower()
