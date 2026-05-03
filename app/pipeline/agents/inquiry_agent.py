"""LLM agent that recovers failed supplier inquiries via the browse CLI.

Used as a fallback when the deterministic Playwright flow fails (typically
due to captchas or unexpected page state). Gets dropped into the existing
browser session at the point of failure and uses CLI commands to diagnose
and complete the inquiry.

Large tool outputs (snapshots, page text) are automatically evicted to disk
by the Agent subclass — the agent gets grep/read tools to explore."""

import logging
import os
import re
import shlex
import subprocess
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.base.config import PROJECT_ROOT, browserbase_settings, model_settings
from app.base.llm import Agent, get_model

log = logging.getLogger(__name__)

BROWSE_BIN = str(PROJECT_ROOT / "node_modules" / ".bin" / "browse")

SYSTEM_PROMPT = """\
You are a browser automation recovery agent. A coded automation flow tried to \
send a supplier inquiry on Alibaba.com but failed. You have been dropped into \
the exact browser session where it got stuck.

Your job: figure out what went wrong and complete the inquiry.

## Tools

**browse** — run a CLI command against the browser session:
- snapshot              — accessibility tree (filtered to page content)
- click <ref>           — click by ref from snapshot (e.g. "click @0-5")
- click_xy <x> <y>     — click at exact pixel coordinates
- fill <selector> <value> — fill an input (CSS selector)
- type <text>           — type into focused element
- press <key>           — press key (Enter, Tab, Escape, Ctrl+A, etc.)
- open <url>            — navigate to URL
- get url               — current URL
- get text [selector]   — text content
- scroll <x> <y> <dx> <dy> — scroll
- drag <x1> <y1> <x2> <y2> — drag (slider captchas)
- wait load|selector|timeout [arg]
- eval <js-expression>

When output is large, it is automatically evicted to disk and replaced with a \
blob ID. Use the eviction tools to explore:

**grep_evicted_result** — search an evicted result by blob_id and regex pattern.
**read_evicted_result** — read a line range from an evicted result by blob_id.

## Workflow

1. Run `snapshot` to see current page state.
2. If the result was evicted, grep for key elements ("Send inquiry", \
   "textarea", "captcha", "Start order") to orient yourself.
3. Act on what you find, then snapshot again to verify.

## Rules

- Do NOT modify the inquiry message — paste it exactly as provided.
- If the page shows a captcha/slider, try to solve it.
- If you see a "Start order" button but no inquiry option → return WHOLESALE.
- If you can't complete after 3 attempts → return FAILED with a reason."""


_I18N_PATTERN = re.compile(r'"intl-|gangesweb|"i18n')


def _strip_i18n(output: str) -> str:
    """Remove i18n/localisation noise lines from snapshot output."""
    return "\n".join(
        line for line in output.splitlines()
        if not _I18N_PATTERN.search(line)
    )


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



def _make_tools(session_id: str) -> list[Tool]:
    env = {
        **os.environ,
        "BROWSERBASE_API_KEY": browserbase_settings.BROWSERBASE_API_KEY,
        "BROWSERBASE_PROJECT_ID": browserbase_settings.BROWSERBASE_PROJECT_ID,
    }

    def browse(command: str) -> str:
        """Run a browse CLI command against the live browser session."""
        parts = shlex.split(command)
        if not parts:
            return "ERROR: empty command"

        if parts[0] == "snapshot" and "--compact" not in parts:
            parts.append("--compact")

        args = [BROWSE_BIN, "--connect", session_id] + parts
        log.info("browse CLI: %s", " ".join(args[2:]))
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=60, env=env,
        )
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\nERROR: {result.stderr}"
        if not output:
            return "(no output)"

        if parts[0] == "snapshot":
            output = _strip_i18n(output)

        return output

    return [Tool(browse, takes_ctx=False)]


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
        tools=_make_tools(session_id),
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
