import logging
from urllib.parse import urlparse, parse_qs

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from app.base.config import google_settings, settings
from app.db.database import SessionLocal
from app.db.models.keyvalue import KeyValue
from app.services.browser import BrowserSession

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.modify",
]

REDIRECT_URI = "http://localhost:8085"
DB_KEY = "google_refresh_token"


def _load_refresh_token_from_db() -> str | None:
    with SessionLocal() as session:
        row = session.get(KeyValue, DB_KEY)
        if row:
            return row.value.get("refresh_token")
    return None


def _save_refresh_token_to_db(refresh_token: str) -> None:
    with SessionLocal() as session:
        row = session.get(KeyValue, DB_KEY)
        if row:
            row.value = {"refresh_token": refresh_token}
        else:
            row = KeyValue(key=DB_KEY, value={"refresh_token": refresh_token})
            session.add(row)
        session.commit()


def _try_refresh(refresh_token: str) -> Credentials | None:
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=google_settings.GOOGLE_TOKEN_URI,
        client_id=google_settings.GOOGLE_CLIENT_ID,
        client_secret=google_settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
        return creds
    except RefreshError:
        log.warning("Refresh token invalid or expired")
        return None


def _build_auth_url() -> str:
    scope_str = " ".join(SCOPES)
    return (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={google_settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope_str}"
        f"&access_type=offline"
        f"&prompt=consent"
    )


def _exchange_code_for_tokens(code: str) -> dict:
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": google_settings.GOOGLE_CLIENT_ID,
            "client_secret": google_settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    return resp.json()


def _log_page_state(page, step: str) -> None:
    buttons = [
        b.text_content().strip()
        for b in page.locator("button").all()
        if b.is_visible() and b.text_content().strip()
    ]
    links = [
        a.text_content().strip()
        for a in page.locator("a").all()
        if a.is_visible() and a.text_content().strip()
    ]
    checkboxes = page.locator('input[type="checkbox"]')
    cb_count = sum(1 for i in range(checkboxes.count()) if checkboxes.nth(i).is_visible())
    log.info(
        "[%s] url=%s buttons=%s links=%s checkboxes=%d",
        step, page.url[:100], buttons, links, cb_count,
    )


def _authenticate_via_browser() -> Credentials:
    """Use Browserbase to complete the Google OAuth flow."""
    log.info("No valid refresh token — authenticating via Browserbase")
    auth_url = _build_auth_url()

    with BrowserSession() as s:
        page = s.page

        redirect_url = None

        def capture_redirect(request):
            nonlocal redirect_url
            if request.url.startswith(REDIRECT_URI):
                redirect_url = request.url

        page.on("request", capture_redirect)

        page.goto(auth_url, wait_until="networkidle")
        _log_page_state(page, "login_page")

        page.fill('input[type="email"]', settings.GMAIL_ACCOUNT)
        page.click("#identifierNext")
        page.wait_for_selector('input[type="password"]', state="visible")

        page.fill('input[type="password"]', settings.GMAIL_PASSWORD)
        page.click("#passwordNext")

        page.locator('a:has-text("Advanced")').wait_for(
            state="visible", timeout=30_000
        )
        _log_page_state(page, "warning_page")

        page.locator('a:has-text("Advanced")').click()
        page.locator('a:has-text("unsafe")').wait_for(state="visible")
        page.locator('a:has-text("unsafe")').click()

        continue_btn = page.locator('button:has-text("Continue")')
        continue_btn.wait_for(state="visible", timeout=30_000)
        _log_page_state(page, "consent_page")
        continue_btn.click()
        page.wait_for_timeout(10_000)
        _log_page_state(page, "after_consent")

    if not redirect_url:
        raise RuntimeError("OAuth flow did not produce a redirect with auth code")

    qs = parse_qs(urlparse(redirect_url).query)
    code = qs["code"][0]

    token_data = _exchange_code_for_tokens(code)
    refresh_token = token_data["refresh_token"]
    _save_refresh_token_to_db(refresh_token)
    log.info("New refresh token saved to database")

    return Credentials(
        token=token_data["access_token"],
        refresh_token=refresh_token,
        token_uri=google_settings.GOOGLE_TOKEN_URI,
        client_id=google_settings.GOOGLE_CLIENT_ID,
        client_secret=google_settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )


def get_google_credentials() -> Credentials:
    # 1. Try refresh token from DB
    db_token = _load_refresh_token_from_db()
    if db_token:
        creds = _try_refresh(db_token)
        if creds:
            return creds

    # 2. Try refresh token from env
    if google_settings.GOOGLE_REFRESH_TOKEN:
        creds = _try_refresh(google_settings.GOOGLE_REFRESH_TOKEN)
        if creds:
            _save_refresh_token_to_db(google_settings.GOOGLE_REFRESH_TOKEN)
            return creds

    # 3. Authenticate via Browserbase
    return _authenticate_via_browser()
