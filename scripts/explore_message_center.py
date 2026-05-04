"""Test the deterministic Alibaba message center read.

Authenticates, opens a BrowserSession, and runs read_platform_messages()
against the live message center. Prints what it finds."""

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


def main():
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

        log.info("Running deterministic read...")
        from app.services.platforms.alibaba.messaging import read_platform_messages

        messages = read_platform_messages(browser.page, unread_only=False)

        print("\n" + "=" * 60)
        print(f"MESSAGES FOUND: {len(messages)}")
        for i, msg in enumerate(messages, 1):
            print(f"\n--- Message {i} ---")
            print(f"  Supplier: {msg['supplier_name']}")
            print(f"  Product: {msg['product_url']}")
            print(f"  URL: {msg['conversation_url']}")
            print(f"  Sent at: {msg['sent_at']}")
            print(f"  Text: {msg['message_text']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
