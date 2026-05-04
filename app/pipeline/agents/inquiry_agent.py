"""LLM agent that recovers failed supplier inquiries via the browse CLI.

Used as a fallback when the deterministic Playwright flow fails (typically
due to captchas or unexpected page state). Gets dropped into the existing
browser session at the point of failure and uses CLI commands to diagnose
and complete the inquiry.

Large tool outputs (snapshots, page text) are automatically evicted to disk
by the Agent subclass — the agent gets grep/read tools to explore."""

import logging
import re
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Tool
from pydantic_ai.messages import ModelMessage

from playwright.sync_api import sync_playwright

from app.base.config import model_settings
from app.base.llm import Agent, get_model
from app.services.browser import BROWSE_TOOL_DOCS, BrowseToolkit, bb
from app.services.platforms.alibaba.service import (
    INQUIRY_SUBMIT,
    INQUIRY_TEXTAREA,
    _JS_FILL_AND_SUBMIT,
    _get_inquiry_frame,
    _wait_for_submit_confirmation,
    SUBMIT_CONFIRM_TIMEOUT,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""\
You are a browser automation recovery agent. A coded automation flow tried to \
send a supplier inquiry on Alibaba.com but failed. You have been dropped into \
the exact browser session where it got stuck.

Your job: figure out what went wrong and complete the inquiry.

{BROWSE_TOOL_DOCS}

## Workflow

1. Take a `screenshot` to understand the current page state.
2. Identify what needs to be clicked or filled.
3. Use `click` with a text or CSS selector to interact:
   - `click "text=Send inquiry"`
   - `click "button:has-text('Chat now')"`
   - `click "#submit-btn"`
4. Take another screenshot to verify the result.
5. If a selector doesn't work, use `snapshot` to get the accessibility tree \
   and then `click @<ref>` with the ref number.

**submit_inquiry_iframe** — once the inquiry modal is open, call this to fill \
and submit the form inside the iframe. This handles the iframe boundary that \
normal CSS selectors can't cross. Pass the exact inquiry message as the argument.

## Finishing

When done, you MUST call the `finish` tool with the outcome:
- **SENT** — only if you have visual confirmation the inquiry was submitted \
  (e.g. a success message, "Your inquiry has been sent", the form disappeared \
  after clicking Send, or the page navigated to a confirmation).
- **WHOLESALE** — if you see a "Start order" button but no inquiry option.
- **LOGIN_REQUIRED** — if the page shows a login/sign-in form, "Continue with \
  Google", or any authentication prompt. Do NOT attempt to log in yourself — \
  call finish immediately with LOGIN_REQUIRED so the system can re-authenticate.
- **FAILED** — if you can't complete the inquiry. Include a reason.

Never assume success. If you're unsure whether the inquiry was sent, take a \
screenshot to verify before calling finish.

## Rules

- Do NOT modify the inquiry message — paste it exactly as provided.
- If the page shows a captcha/slider, try to solve it using click_xy and drag.
- If you can't complete after 3 attempts → call finish with FAILED.
- Do NOT use `wait timeout` to stall — act immediately after each step.
- Prefer the platform-specific CSS selectors over text matching or snapshots."""


class InquiryStatus(str, Enum):
    SENT = "SENT"
    WHOLESALE = "WHOLESALE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    FAILED = "FAILED"


class InquiryResult(BaseModel):
    status: InquiryStatus = Field(description="Outcome of the inquiry attempt")
    reason: str = Field(
        default="",
        description="Explanation if failed or wholesale, empty on success",
    )


def _make_inquiry_tools(
    toolkit: BrowseToolkit, result_holder: list[InquiryResult]
) -> list[Tool]:
    """Build inquiry-specific tools on top of the shared BrowseToolkit."""
    session_id = toolkit.session_id

    def submit_inquiry_iframe(message: str) -> str:
        """Fill and submit the Alibaba inquiry iframe form. Call this once the
        inquiry modal is open (after clicking the page-level Send Inquiry button).
        Connects directly to the browser session, finds the iframe, fills the
        message, and clicks submit.

        Args:
            message: The inquiry message to send.
        """
        log.info("submit_inquiry_iframe: connecting to session %s", session_id)
        connect_url = bb.sessions.retrieve(session_id).connect_url
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(connect_url)
            page = browser.contexts[0].pages[0]

            frame = _get_inquiry_frame(page)
            iframe_pattern = re.compile(r"message\.alibaba\.com")

            result = frame.evaluate(
                _JS_FILL_AND_SUBMIT,
                {
                    "textareaSel": INQUIRY_TEXTAREA,
                    "submitSel": INQUIRY_SUBMIT,
                    "message": message,
                },
            )

            if not result.get("ok"):
                return f"FAILED: {result.get('reason')} (step: {result.get('step')})"

            if _wait_for_submit_confirmation(
                page, iframe_pattern, SUBMIT_CONFIRM_TIMEOUT
            ):
                return "SUCCESS: inquiry submitted and confirmed"
            return "UNCERTAIN: submit clicked but not confirmed"
        except TimeoutError:
            return "FAILED: inquiry iframe not found — is the modal open?"
        except Exception as exc:
            if "Execution context was destroyed" in str(exc):
                return "SUCCESS: iframe navigated away (inquiry sent)"
            return f"FAILED: {exc}"
        finally:
            browser.close()
            pw.stop()

    finish = toolkit.make_finish_tool(InquiryStatus, InquiryResult, result_holder)

    return toolkit.tools() + [
        Tool(submit_inquiry_iframe, takes_ctx=False),
        finish,
    ]


def send_inquiry_via_agent(
    session_id: str,
    product_url: str,
    message: str,
    *,
    cleanup: bool = True,
    platform_prompt: str = "",
) -> InquiryResult:
    """Recover a failed inquiry using an LLM agent with browse CLI tools."""
    result_holder: list[InquiryResult] = []
    toolkit = BrowseToolkit(session_id)

    system_prompt = SYSTEM_PROMPT
    if platform_prompt:
        system_prompt = f"{SYSTEM_PROMPT}\n\n{platform_prompt}"

    agent = Agent(
        model=get_model(model_settings.MODERATE),
        name="inquiry_agent",
        system_prompt=system_prompt,
        tools=_make_inquiry_tools(toolkit, result_holder),
        retries=5,
        cleanup=cleanup,
        model_settings={"thinking": "high"},
    )

    prompt = (
        f"The coded flow failed while trying to send an inquiry on this product:\n"
        f"URL: {product_url}\n\n"
        f"Message to send (copy exactly, do not modify):\n"
        f"---\n{message}\n---\n\n"
        f"Start with `screenshot` to see where the browser is stuck."
    )

    run_result = agent.run_sync(prompt)

    if not result_holder:
        log.warning("Agent did not call finish — running classification fallback")
        return BrowseToolkit.classify_from_history(
            run_result.all_messages(), system_prompt, InquiryResult, "inquiry_fallback"
        )

    result = result_holder[-1]
    log.info(
        "Inquiry agent result for %s: %s %s",
        product_url,
        result.status,
        result.reason[:200],
    )
    return result
