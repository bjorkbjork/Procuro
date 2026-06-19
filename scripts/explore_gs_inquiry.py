"""Explore the GlobalSources inquiry flow via the LLM agent.

Authenticates (or uses pre-authed context), opens a BrowserSession to a GS
product page, and runs the inquiry agent with GS-specific prompts. Watch the
live Browserbase URL to observe what the agent clicks, then use those
observations to write deterministic Playwright scripts.

Usage:
    python scripts/explore_gs_inquiry.py <product_url>
    python scripts/explore_gs_inquiry.py  # uses default test URL
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from app.base.config import browserbase_settings, settings
from app.pipeline.agents.inquiry_agent import send_inquiry_via_agent
from app.services.browser import BrowserSession, authenticate_platform, bb
from app.services.platforms.globalsources import Platform

log = logging.getLogger(__name__)

DEFAULT_PRODUCT_URL = (
    "https://www.globalsources.com/OLED-TV/55-inch-smart-tv-1212888159p.htm"
)

TEST_MESSAGE = f"""\
Hi,

We are {settings.AGENT_COMPANY_DESCRIPTION}. We are looking to source this product. \
Please provide your best pricing, lead time and MOQ for an order of 500+ units.

Many Thanks, {settings.AGENT_NAME}."""


def main():
    product_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PRODUCT_URL
    platform = Platform()

    log.info("Authenticating on GlobalSources...")
    try:
        context_id = authenticate_platform(platform)
    except Exception:
        log.warning(
            "Auth failed — creating unauthenticated context. "
            "Inquiry may hit a login wall (agent will report LOGIN_REQUIRED)."
        )
        from app.services.browser import create_context

        context_id = create_context()

    log.info("Auth context: %s", context_id)

    session = bb.sessions.create(
        project_id=browserbase_settings.BROWSERBASE_PROJECT_ID,
        keep_alive=True,
        region="ap-southeast-1",
        proxies=[
            {"type": "browserbase", "geolocation": {"country": "AU", "city": "SYDNEY"}}
        ],
        browser_settings={
            "context": {"id": context_id, "persist": False},
        },
    )

    print("\n" + "=" * 60)
    print(f"SESSION ID: {session.id}")
    print(f"PRODUCT URL: {product_url}")
    print("=" * 60)
    print("\nWatch the agent in real-time on the Browserbase dashboard.")
    print("The agent will attempt to send an inquiry on the product page.\n")

    log.info("Running inquiry agent with GS prompts...")
    result = send_inquiry_via_agent(
        session.id,
        product_url,
        TEST_MESSAGE,
        cleanup=False,
        platform_prompt=platform.inquiry_agent_prompt,
    )

    print("\n" + "=" * 60)
    print(f"RESULT: {result.status}")
    print(f"REASON: {result.reason}")
    print("=" * 60)

    # Save the final page HTML as a fixture
    try:
        connect_url = bb.sessions.retrieve(session.id).connect_url
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(connect_url)
        page = browser.contexts[0].pages[0]
        html = page.content()
        fixture_path = "html_test_fixtures/gs_inquiry_final_state.html"
        with open(fixture_path, "w") as f:
            f.write(html)
        log.info("Saved final page HTML to %s", fixture_path)
        browser.close()
        pw.stop()
    except Exception:
        log.exception("Could not save final page HTML")

    bb.sessions.update(session.id, status="REQUEST_RELEASE")


if __name__ == "__main__":
    main()
