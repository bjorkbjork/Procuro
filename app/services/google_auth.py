"""Google OAuth credential management. Attempts token refresh from DB, then .env,
and falls back to a fully automated browser login via Browserbase if both fail.
The refresh token is persisted to Postgres so re-auth survives restarts."""

import logging
import time
from urllib.parse import urlparse, parse_qs

import pyotp
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from playwright.sync_api import Page

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


CHALLENGE_POLL_INTERVAL = 10
CHALLENGE_TIMEOUT = 300

TOTP_INPUT_SELECTORS = [
    "input[name='totpPin']",
    "input#totpPin",
]

CHALLENGE_URL_MARKER = "accounts.google.com/v3/signin/challenge"


def _on_challenge_page(page: Page) -> bool:
    """Check if we're on a Google auth challenge page."""
    try:
        if page.is_closed():
            return False
        return CHALLENGE_URL_MARKER in page.url
    except Exception:
        return False


def _try_totp(page: Page) -> bool:
    """If a TOTP input is visible and we have a secret, fill it. Returns True if handled."""
    if not settings.GOOGLE_TOTP_SECRET:
        return False

    for selector in TOTP_INPUT_SELECTORS:
        inp = page.locator(selector)
        if inp.count() > 0 and inp.first.is_visible():
            secret = settings.GOOGLE_TOTP_SECRET.replace(" ", "")
            code = pyotp.TOTP(secret).now()
            log.info("Filling TOTP code")
            inp.first.fill(code)
            page.locator("button#totpNext, button:has-text('Next')").first.click()
            page.wait_for_timeout(3_000)
            return True
    return False


CHALLENGE_HEADINGS = ["verify it", "2-step verification", "confirm your identity"]


def _get_challenge_description(page: Page) -> str:
    """Extract the challenge heading (e.g. 'Verify it's you')."""
    try:
        for tag in ["h1", "h2", "[role='heading']"]:
            el = page.locator(tag)
            for i in range(el.count()):
                text = el.nth(i).text_content().strip()
                if text and any(m in text.lower() for m in CHALLENGE_HEADINGS):
                    return text
        visible = page.inner_text("body").lower()
        for marker in CHALLENGE_HEADINGS:
            if marker in visible:
                return marker.title()
    except Exception:
        pass
    return "Unknown auth challenge"


def _handle_auth_challenges(page: Page, session_url: str) -> None:
    """Handle Google auth challenges after password entry.

    Attempts TOTP automatically if configured. Falls back to emailing
    the maintainer a live session link for manual resolution.
    """
    alerted = False
    totp_attempted = False

    deadline = time.monotonic() + CHALLENGE_TIMEOUT
    while time.monotonic() < deadline:
        if page.is_closed():
            log.info("Auth popup closed — login resolved")
            return

        try:
            on_challenge = _on_challenge_page(page)
        except Exception:
            log.info("Challenge page closed — auth resolved")
            return

        if not on_challenge:
            log.info("Auth challenge cleared (navigated away from challenge)")
            return

        if not totp_attempted and _try_totp(page):
            totp_attempted = True
            continue

        if not alerted:
            description = _get_challenge_description(page)
            log.warning("Google auth challenge requires manual action: %s", description)
            from app.services.gmail import GmailService
            gmail = GmailService()
            gmail.send_email(
                to=settings.MAINTAINER_EMAIL_ADDRESS,
                subject=f"[Google Auth] {description}",
                body=(
                    f"Google is blocking login with a challenge:\n\n"
                    f"  {description}\n\n"
                    f"Resolve it here:\n{session_url}\n\n"
                    f"The agent will resume automatically once the challenge clears."
                ),
            )
            log.info("Challenge alert sent to %s", settings.MAINTAINER_EMAIL_ADDRESS)
            alerted = True

        time.sleep(CHALLENGE_POLL_INTERVAL)

    raise RuntimeError("Google auth challenge not resolved within %ds" % CHALLENGE_TIMEOUT)


def google_login(page: Page, session_url: str = "") -> None:
    """Fill Google email/password on the current page, handling auth challenges.

    Expects the page to already be on a Google sign-in form with email input visible.
    """
    page.wait_for_selector("input[type='email']", timeout=15_000)
    page.fill("input[type='email']", settings.GMAIL_ACCOUNT)
    page.click("#identifierNext")

    page.wait_for_selector("input[type='password']:visible", timeout=15_000)
    page.fill("input[type='password']", settings.GMAIL_PASSWORD)
    page.click("#passwordNext")

    page.wait_for_timeout(3_000)
    _handle_auth_challenges(page, session_url)


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

        google_login(page)

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
