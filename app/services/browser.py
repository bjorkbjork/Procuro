from browserbase import Browserbase
from playwright.sync_api import Browser, Page, sync_playwright

from app.base.config import browserbase_settings

bb = Browserbase(api_key=browserbase_settings.BROWSERBASE_API_KEY)


class BrowserSession:
    def __init__(self, proxy_country: str | None = None):
        self._proxy_country = proxy_country
        self._pw = None
        self._browser: Browser | None = None
        self.page: Page | None = None

    def __enter__(self) -> "BrowserSession":
        proxies = None
        if self._proxy_country:
            proxies = [{"type": "browserbase", "geolocation": {"country": self._proxy_country}}]

        session = bb.sessions.create(
            project_id=browserbase_settings.BROWSERBASE_PROJECT_ID,
            proxies=proxies,
        )
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
        return False
