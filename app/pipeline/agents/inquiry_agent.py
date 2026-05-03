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
import uuid
from enum import Enum

import stamina
from pydantic import BaseModel, Field
from pydantic_ai import Agent as _BaseAgent, ModelRetry, Tool
from pydantic_ai.messages import BinaryContent, ModelMessage

from playwright.sync_api import sync_playwright

from app.base.config import PROJECT_ROOT, browserbase_settings, model_settings
from app.base.llm import EVICTION_DIR, Agent, get_model
from app.services.browser import bb
from app.services.platforms.alibaba.service import (
    INQUIRY_SUBMIT,
    INQUIRY_TEXTAREA,
    _JS_FILL_AND_SUBMIT,
    _get_inquiry_frame,
    _wait_for_submit_confirmation,
    SUBMIT_CONFIRM_TIMEOUT,
)

log = logging.getLogger(__name__)

BROWSE_BIN = str(PROJECT_ROOT / "node_modules" / ".bin" / "browse")

SYSTEM_PROMPT = """\
You are a browser automation recovery agent. A coded automation flow tried to \
send a supplier inquiry on Alibaba.com but failed. You have been dropped into \
the exact browser session where it got stuck.

Your job: figure out what went wrong and complete the inquiry.

## Tools

**screenshot** — capture a screenshot of the current page. Use this to \
understand what's on screen and what state the page is in.

**browse** — run a CLI command against the browser session:
- click <selector>         — click by CSS selector, XPath, or snapshot ref.
                             Examples: click "text=Send inquiry"
                                       click "button:has-text('Send')"
                                       click @0-5  (ref from snapshot)
- click_xy <x> <y>        — click at CSS pixel coordinates (ONLY for captcha \
                             sliders or elements with no text/selector)
- snapshot                 — accessibility tree with clickable refs
- fill <selector> <value>  — fill an input (CSS selector)
- type <text>              — type into focused element
- press <key>              — press key (Enter, Tab, Escape, Ctrl+A, etc.)
- open <url>               — navigate to URL
- get url                  — current URL
- get text [selector]      — text content
- scroll <x> <y> <dx> <dy> — scroll
- drag <x1> <y1> <x2> <y2> — drag (captcha sliders)
- wait load|selector|timeout [arg]
- eval <js-expression>

When text output is large, it is automatically evicted to disk and replaced \
with a blob ID. Use the eviction tools to explore:

**grep_evicted_result** — search an evicted text result by blob_id and regex.
**read_evicted_result** — read a line range from an evicted text result.

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

**IMPORTANT**: Do NOT use `click_xy` for buttons, links, or form elements — \
coordinates from screenshots are unreliable. Use text/CSS selectors or \
snapshot refs instead. Reserve `click_xy` only for captcha sliders or drag \
operations where no selector exists.

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


_I18N_PATTERN = re.compile(r'"intl-|gangesweb|"i18n')


def _strip_i18n(output: str) -> str:
    """Remove i18n/localisation noise lines from snapshot output."""
    return "\n".join(
        line for line in output.splitlines() if not _I18N_PATTERN.search(line)
    )


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


def _make_tools(session_id: str, result_holder: list[InquiryResult]) -> list[Tool]:
    env = {
        **os.environ,
        "BROWSERBASE_API_KEY": browserbase_settings.BROWSERBASE_API_KEY,
        "BROWSERBASE_PROJECT_ID": browserbase_settings.BROWSERBASE_PROJECT_ID,
    }

    def _run_browse(
        parts: list[str], timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        verb, rest = parts[0], parts[1:]
        # Insert -- to stop flag parsing (e.g. scroll's -200), but skip
        # for fill/type (value is already one arg) and when rest has
        # real flags like --compact
        needs_separator = (
            rest
            and verb not in ("fill", "type")
            and not any(a.startswith("--") for a in rest)
        )
        if needs_separator:
            args = [BROWSE_BIN, "--connect", session_id, verb, "--"] + rest
        else:
            args = [BROWSE_BIN, "--connect", session_id] + parts
        log.info("browse CLI: %s", " ".join(args[2:]))
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=env
        )
        if result.returncode != 0:
            log.warning(
                "browse CLI exit %d: stderr=%s", result.returncode, result.stderr[:500]
            )
        elif result.stdout:
            log.debug(
                "browse CLI stdout (%d chars): %s",
                len(result.stdout),
                result.stdout[:200],
            )
        return result

    @stamina.retry(on=(subprocess.TimeoutExpired, OSError), attempts=3, timeout=90)
    def screenshot(reason: str) -> BinaryContent | str:
        """Capture a screenshot of the current browser page.

        Args:
            reason: Why you are taking this screenshot (e.g. "checking if modal opened after click").
        """
        log.info("Screenshot reason: %s", reason)
        EVICTION_DIR.mkdir(exist_ok=True)
        fpath = EVICTION_DIR / f"screenshot_{uuid.uuid4().hex[:8]}.png"
        try:
            result = _run_browse(["screenshot", str(fpath)], timeout=30)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.error("Screenshot transient error (stamina will retry): %s", exc)
            raise
        if result.returncode != 0:
            msg = f"Screenshot failed: {result.stderr}"
            log.warning("Screenshot ModelRetry: %s", msg)
            raise ModelRetry(msg)
        data = fpath.read_bytes()
        log.info("Screenshot captured: %s (%d bytes)", fpath.name, len(data))
        return BinaryContent(data=data, media_type="image/png")

    @stamina.retry(on=(subprocess.TimeoutExpired, OSError), attempts=3, timeout=90)
    def browse(command: str, reason: str) -> str:
        """Run a browse CLI command against the live browser session.

        Args:
            command: The browse CLI command to run.
            reason: Why you are running this command (e.g. "clicking Send Inquiry button").
        """
        log.info("Browse reason: %s", reason)
        stripped = command.strip()
        if not stripped:
            return "ERROR: empty command"

        # fill/type carry free-form text — shlex.split would shred it
        if stripped.startswith(("fill ", "type ")):
            verb, rest = stripped.split(None, 1)
            if verb == "fill":
                selector, value = rest.split(None, 1)
                parts = [verb, selector, value]
            else:
                parts = [verb, rest]
        else:
            parts = shlex.split(stripped)

        if parts[0] == "snapshot" and "--compact" not in parts:
            parts.append("--compact")

        try:
            result = _run_browse(parts)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.error(
                "Browse '%s' transient error (stamina will retry): %s", command, exc
            )
            raise
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            msg = f"Command '{command}' failed: {result.stderr}"
            log.warning("Browse ModelRetry: %s", msg)
            raise ModelRetry(msg)
        if not output:
            return "(no output)"

        if parts[0] == "snapshot":
            output = _strip_i18n(output)

        return output

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

    # We use a manual finish tool instead of PydanticAI structured output
    # because structured output uses required tool_choice under the hood,
    # which is incompatible with thinking mode on AWS Bedrock. Prompting
    # the agent to call finish is good enough.
    def finish(status: InquiryStatus, reason: str = "") -> str:
        """Report the outcome of the inquiry attempt. You MUST call this when done.

        Args:
            status: The outcome — SENT, WHOLESALE, LOGIN_REQUIRED, or FAILED.
            reason: Explanation (required for WHOLESALE/FAILED, optional for SENT).
        """
        result_holder.append(InquiryResult(status=status, reason=reason))
        log.info("Agent finished: status=%s reason=%s", status, reason[:200])
        return f"Recorded: {status.value}"

    return [
        Tool(screenshot, takes_ctx=False),
        Tool(browse, takes_ctx=False),
        Tool(submit_inquiry_iframe, takes_ctx=False),
        Tool(finish, takes_ctx=False),
    ]


def _classify_from_history(
    messages: list[ModelMessage], system_prompt: str
) -> InquiryResult:
    """Fallback: pass the agent's conversation to an identical agent with no
    tools and structured output to classify the outcome."""
    fallback = _BaseAgent(
        model=get_model(model_settings.MODERATE),
        name="inquiry_fallback",
        system_prompt=system_prompt,
        output_type=InquiryResult,
        retries=2,
    )
    result = fallback.run_sync(
        "You did not call finish. Based on your conversation above, classify the outcome now.",
        message_history=messages,
    )
    log.info(
        "Fallback classification: %s %s",
        result.output.status,
        result.output.reason[:200],
    )
    return result.output


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
    system_prompt = SYSTEM_PROMPT
    if platform_prompt:
        system_prompt = f"{SYSTEM_PROMPT}\n\n{platform_prompt}"
    agent = Agent(
        model=get_model(model_settings.MODERATE),
        name="inquiry_agent",
        system_prompt=system_prompt,
        tools=_make_tools(session_id, result_holder),
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
        return _classify_from_history(run_result.all_messages(), system_prompt)

    result = result_holder[-1]
    log.info(
        "Inquiry agent result for %s: %s %s",
        product_url,
        result.status,
        result.reason[:200],
    )
    return result
