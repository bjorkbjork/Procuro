"""LLM agent that recovers failed supplier inquiries via the browse CLI.

Used as a fallback when the deterministic Playwright flow fails (typically
due to captchas or unexpected page state). Gets dropped into the existing
browser session at the point of failure and uses CLI commands to diagnose
and complete the inquiry."""

import logging
import os
import shlex
import subprocess
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, Tool

from app.base.config import browserbase_settings, model_settings
from app.base.llm import get_model

log = logging.getLogger(__name__)

BROWSE_BIN = str(Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "browse")

SYSTEM_PROMPT = """\
You are a browser automation recovery agent. A coded automation flow tried to \
send a supplier inquiry on Alibaba.com but failed. You have been dropped into \
the exact browser session where it got stuck.

Your job: figure out what went wrong and complete the inquiry.

Available browse commands (pass as the `command` argument):
- snapshot              — get accessibility tree (your primary way to "see" the page)
- screenshot [path]     — save screenshot to file
- click <ref>           — click element by ref from snapshot (e.g. "click @0-5")
- click_xy <x> <y>     — click at exact pixel coordinates
- fill <selector> <value> — fill an input field (CSS selector)
- type <text>           — type text into the currently focused element
- press <key>           — press a key (Enter, Tab, Escape, Ctrl+A, etc.)
- open <url>            — navigate to URL
- get url               — get current URL
- get text [selector]   — get text content of page or element
- scroll <x> <y> <dx> <dy> — scroll at coordinates
- drag <x1> <y1> <x2> <y2> — drag between points (for slider captchas)
- wait load|selector|timeout [arg] — wait for a condition
- eval <js-expression>  — evaluate JavaScript in the page

Start by running `snapshot` to see the current page state, then decide what \
to do.

Rules:
- Do NOT modify the inquiry message — paste it exactly as provided.
- If the page shows a captcha/slider, try to solve it.
- If you see a "Start order" button but no inquiry option, the product is \
  wholesale-only — return WHOLESALE.
- If you can't complete after 3 attempts, return FAILED with a reason.
- Be methodical: snapshot → act → snapshot to verify."""


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


def _make_browse_tool(session_id: str) -> Tool:
    env = {
        **os.environ,
        "BROWSERBASE_API_KEY": browserbase_settings.BROWSERBASE_API_KEY,
        "BROWSERBASE_PROJECT_ID": browserbase_settings.BROWSERBASE_PROJECT_ID,
    }

    def browse(command: str) -> str:
        """Run a browse CLI command against the live browser session.

        Pass the command exactly as you would on the command line after `browse`,
        e.g. "snapshot", "click @0-5", "fill #message 'hello'".
        """
        args = [BROWSE_BIN, "--connect", session_id] + shlex.split(command)
        log.debug("browse CLI: %s", " ".join(args))
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=60, env=env,
        )
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\nERROR: {result.stderr}"
        return output or "(no output)"

    return Tool(browse, takes_ctx=False)


def send_inquiry_via_agent(
    session_id: str,
    product_url: str,
    message: str,
) -> InquiryResult:
    """Recover a failed inquiry using an LLM agent with browse CLI tools."""
    agent = Agent(
        model=get_model(model_settings.CHEAP),
        system_prompt=SYSTEM_PROMPT,
        output_type=InquiryResult,
        tools=[_make_browse_tool(session_id)],
        retries=2,
    )

    prompt = (
        f"The coded flow failed while trying to send an inquiry on this product:\n"
        f"URL: {product_url}\n\n"
        f"Message to send (copy exactly, do not modify):\n"
        f"---\n{message}\n---\n\n"
        f"Start with `snapshot` to see where the browser is stuck."
    )

    result = agent.run_sync(prompt)
    log.info(
        "Inquiry agent result for %s: %s %s",
        product_url,
        result.output.status,
        result.output.reason,
    )
    return result.output
