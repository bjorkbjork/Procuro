"""Explore the GlobalSources message center via the LLM agent.

Authenticates (or uses pre-authed context), opens a BrowserSession, and runs
the platform inbox reader agent with GS-specific prompts. Watch the live
Browserbase URL to observe how the agent navigates the message center, then
use those observations to write deterministic Playwright scripts.

Usage:
    python scripts/explore_gs_messages.py
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from app.base.config import browserbase_settings
from app.pipeline.agents.platform_message_agent import read_inbox_via_agent
from app.services.browser import authenticate_platform, bb
from app.services.platforms.globalsources import Platform

log = logging.getLogger(__name__)


def main():
    platform = Platform()

    log.info("Authenticating on GlobalSources...")
    try:
        context_id = authenticate_platform(platform)
    except Exception:
        log.warning(
            "Auth failed — creating unauthenticated context. "
            "Message center will likely hit a login wall."
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
    print("=" * 60)
    print("\nWatch the agent in real-time on the Browserbase dashboard.")
    print("The agent will attempt to read the GS message center.\n")

    log.info("Running inbox reader agent with GS prompts...")
    result = read_inbox_via_agent(
        session.id,
        platform_prompt=platform.messaging_agent_prompt,
    )

    print("\n" + "=" * 60)
    print(f"STATUS: {result.status}")
    print(f"REASON: {result.reason}")
    print(f"MESSAGES FOUND: {len(result.messages)}")
    for i, msg in enumerate(result.messages, 1):
        print(f"\n--- Message {i} ---")
        print(f"  Supplier: {msg.supplier_name}")
        print(f"  URL: {msg.conversation_url}")
        print(f"  Text: {msg.message_text}")
    print("=" * 60)

    bb.sessions.update(session.id, status="REQUEST_RELEASE")


if __name__ == "__main__":
    main()
