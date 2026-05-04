"""Test the deterministic Alibaba reply fill.

Usage: pdm run python scripts/explore_platform_reply.py <conversation_url>

Authenticates, navigates to the conversation URL, and fills a test message
into the textarea. Does NOT click send."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from app.services.browser import BrowserSession, authenticate_platform
from app.services.platforms.alibaba import Platform

log = logging.getLogger(__name__)

TEST_MESSAGE = "Thank you for your response. We can confirm delivery to Australia. Could you please provide FOB pricing for 2000 units?"


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <conversation_url>")
        sys.exit(1)

    conversation_url = sys.argv[1]
    platform = Platform()

    log.info("Authenticating on Alibaba...")
    context_id = authenticate_platform(platform)
    log.info("Auth context: %s", context_id)

    with BrowserSession(
        proxy_country="AU",
        context_id=context_id,
    ) as browser:
        log.info("Session: %s", browser.session_id)
        log.info("Live URL: %s", browser.live_url)

        log.info("Filling reply...")
        from app.services.platforms.alibaba.messaging import send_platform_reply

        result = send_platform_reply(browser.page, conversation_url, TEST_MESSAGE)

        print(f"\nResult: {result}")
        print("Check the live URL to verify the textarea was filled.")
        browser.page.wait_for_timeout(30_000)


if __name__ == "__main__":
    main()
