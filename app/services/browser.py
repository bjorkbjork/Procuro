from browserbase import Browserbase
from playwright.sync_api import Browser, Page, sync_playwright

from app.base.config import browserbase_settings

bb = Browserbase(api_key=browserbase_settings.BROWSERBASE_API_KEY)


class BrowserSession:
    def __init__(self, proxy_country: str | None = None, keep_alive: bool = False):
        self._proxy_country = proxy_country
        self._keep_alive = keep_alive
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

    def __enter__(self) -> "BrowserSession":
        proxies = None
        if self._proxy_country:
            proxies = [{"type": "browserbase", "geolocation": {"country": self._proxy_country}}]

        session = bb.sessions.create(
            project_id=browserbase_settings.BROWSERBASE_PROJECT_ID,
            proxies=proxies,
            keep_alive=self._keep_alive,
        )
        self.session_id = session.id
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(session.connect_url)
        context = self._browser.contexts[0]
        self.page = context.pages[0]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        if self.session_id:
            bb.sessions.update(self.session_id, status="REQUEST_RELEASE")
        return False
