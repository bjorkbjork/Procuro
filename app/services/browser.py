"""Browserbase session manager. Wraps Playwright over a cloud browser with
optional geo-proxying and automatic captcha handling on every navigation."""

import logging

from browserbase import Browserbase
from playwright.sync_api import Browser, Page, Response, sync_playwright

from app.base.config import browserbase_settings

log = logging.getLogger(__name__)

bb = Browserbase(api_key=browserbase_settings.BROWSERBASE_API_KEY)


def create_context() -> str:
    """Create a persistent Browserbase context and return its ID."""
    ctx = bb.contexts.create(project_id=browserbase_settings.BROWSERBASE_PROJECT_ID)
    log.info("Created Browserbase context %s", ctx.id)
    return ctx.id


class BrowserSession:
    def __init__(
        self,
        proxy_country: str | None = None,
        keep_alive: bool = False,
        context_id: str | None = None,
        persist_context: bool = False,
    ):
        self._proxy_country = proxy_country
        self._keep_alive = keep_alive
        self._context_id = context_id
        self._persist_context = persist_context
        self._pw = None
        self._browser: Browser | None = None
        self.page: Page | None = None
        self.session_id: str | None = None
        self._live_url: str | None = None

    @property
    def live_url(self) -> str | None:
        if self._live_url:
            return self._live_url
        if not self.session_id:
            return None
        debug_info = bb.sessions.debug(self.session_id)
        self._live_url = debug_info.debugger_fullscreen_url
        return self._live_url

    def _handle_captcha(self) -> None:
        from app.services.captcha import handle_captcha

        resolved = handle_captcha(
            page=self.page,
            session_url=self.live_url or "",
            subject=f"Captcha on {self.page.url}",
            message=f"A captcha was encountered navigating to:\n{self.page.url}",
        )
        if not resolved:
            raise RuntimeError(f"Captcha not resolved on {self.page.url}")

    def __enter__(self) -> "BrowserSession":
        proxies = None
        if self._proxy_country:
            proxies = [
                {"type": "browserbase", "geolocation": {"country": self._proxy_country}}
            ]

        create_kwargs = {
            "project_id": browserbase_settings.BROWSERBASE_PROJECT_ID,
            "keep_alive": self._keep_alive,
            "region": "ap-southeast-1",
        }
        if proxies:
            create_kwargs["proxies"] = proxies
        if self._context_id:
            create_kwargs["browser_settings"] = {
                "context": {"id": self._context_id, "persist": self._persist_context},
            }

        session = bb.sessions.create(**create_kwargs)
        self.session_id = session.id
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(session.connect_url)
        context = self._browser.contexts[0]
        self.page = context.pages[0]

        # Wrap page.goto so captcha detection runs after every navigation.
        # Callers never need to handle captchas explicitly.
        original_goto = self.page.goto

        def goto_with_captcha(url, **kwargs) -> Response | None:
            kwargs.setdefault("wait_until", "commit")
            response = original_goto(url, **kwargs)
            self._handle_captcha()
            return response

        self.page.goto = goto_with_captcha

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        if self.session_id:
            bb.sessions.update(self.session_id, status="REQUEST_RELEASE")
        return False
