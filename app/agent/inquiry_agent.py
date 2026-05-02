"""LLM agent that sends a supplier inquiry via the Browserbase MCP server.

Used as a fallback when the deterministic Playwright flow fails (typically
due to captchas). Gets a product URL and auth context_id, spawns its own
MCP-powered browser session, and handles the full flow: navigate, fill
message, solve captcha, submit."""

import logging
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

from app.base.config import browserbase_settings, model_settings
from app.base.llm import get_model

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a browser automation agent that sends product inquiries to suppliers on \
Alibaba.com. You have browser tools to navigate pages, interact with elements, \
and take screenshots.

You will be given a product URL and a message to send. Follow these steps:

1. Navigate to the product URL.
2. Look at the page. If you see a "Start order" button but NO inquiry button, \
   the product is wholesale-only — return status WHOLESALE immediately.
3. Click the inquiry / "Contact supplier" / "Chat now" button to open the \
   inquiry dialog.
4. In the inquiry dialog/iframe, clear any existing text in the message textarea \
   and type the provided message exactly as given. Do not modify it.
5. If there is a captcha or slider verification near the send button, solve it \
   before clicking send.
6. Click the "Send" / "Send inquiry" button.
7. Verify the message was sent — look for a confirmation message, the dialog \
   closing, or the textarea clearing.

Important:
- If the page has a captcha or anti-bot challenge at any point, solve it.
- If the send button is disabled, wait a moment and try again.
- If you cannot send after 3 attempts, return status FAILED with a reason.
- Do NOT make up or modify the message — send it exactly as provided.
- Take a screenshot if you're unsure what's on screen."""


class InquiryStatus(str, Enum):
    SENT = "SENT"
    WHOLESALE = "WHOLESALE"
    FAILED = "FAILED"


class InquiryResult(BaseModel):
    status: InquiryStatus = Field(description="Outcome of the inquiry attempt")
    reason: str = Field(
        default="",
        description="Explanation if failed or wholesale, empty on success",
    )


def send_inquiry_via_agent(
    product_url: str,
    message: str,
    context_id: str,
) -> InquiryResult:
    """Send a supplier inquiry using an LLM agent with Browserbase MCP tools."""
    mcp_server = MCPServerStdio(
        "npx", [
            "@browserbasehq/mcp",
            "--browserbaseApiKey", browserbase_settings.BROWSERBASE_API_KEY,
            "--browserbaseProjectId", browserbase_settings.BROWSERBASE_PROJECT_ID,
            "--contextId", context_id,
            "--proxies",
        ],
        timeout=30,
        read_timeout=300,
    )

    agent = Agent(
        model=get_model(model_settings.CHEAP),
        system_prompt=SYSTEM_PROMPT,
        output_type=InquiryResult,
        toolsets=[mcp_server],
        retries=2,
    )

    prompt = (
        f"Send an inquiry to the supplier on this product page:\n"
        f"URL: {product_url}\n\n"
        f"Message to send (copy exactly, do not modify):\n"
        f"---\n{message}\n---"
    )

    result = agent.run_sync(prompt)
    log.info(
        "Inquiry agent result for %s: %s %s",
        product_url, result.output.status, result.output.reason,
    )
    return result.output
