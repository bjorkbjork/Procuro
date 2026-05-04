"""Browserbase session manager and browse CLI toolkit.

BrowserSession — Playwright over a cloud browser with optional geo-proxying
and automatic captcha handling on every navigation.

BrowseToolkit — browse CLI tools (screenshot, browse command, finish) that
any LLM browser agent can compose with its own task-specific tools. The
finish-tool pattern is required because structured output uses
tool_choice=required, which is incompatible with thinking mode on Bedrock."""

import logging
import os
import re
import shlex
import subprocess
import time
import uuid
from enum import Enum
from typing import Any

import stamina
from browserbase import Browserbase
from pydantic import BaseModel
from pydantic_ai import Agent as _BaseAgent, ModelRetry, Tool
from pydantic_ai.messages import BinaryContent, ModelMessage
from playwright.sync_api import Browser, Page, Response, sync_playwright

from app.base.config import PROJECT_ROOT, browserbase_settings, model_settings
from app.base.llm import EVICTION_DIR, get_model

log = logging.getLogger(__name__)

bb = Browserbase(api_key=browserbase_settings.BROWSERBASE_API_KEY)


def create_context() -> str:
    """Create a persistent Browserbase context and return its ID."""
    ctx = bb.contexts.create(project_id=browserbase_settings.BROWSERBASE_PROJECT_ID)
    log.info("Created Browserbase context %s", ctx.id)
    return ctx.id


def wait_for_session_complete(session_id: str, timeout: float = 30.0) -> None:
    """Poll until a Browserbase session reaches COMPLETED status.

    Context cookies are only persisted once the session fully completes,
    so any session that wrote to a persist-context must be awaited before
    the next session reads from that context.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = bb.sessions.retrieve(session_id).status
        if status == "COMPLETED":
            log.info("Session %s completed", session_id)
            return
        if status in ("ERROR", "TIMED_OUT"):
            log.warning("Session %s ended with status %s", session_id, status)
            return
        time.sleep(1)
    log.warning("Timed out waiting for session %s to complete", session_id)


class BrowserSession:
    def __init__(
        self,
        proxy_country: str | None = None,
        keep_alive: bool = False,
        context_id: str | None = None,
        persist_context: bool = False,
    ):
        self._proxy_country = proxy_country
        self._keep_alive = keep_alive
        self._context_id = context_id
        self._persist_context = persist_context
        self._pw = None
        self._browser: Browser | None = None
        self.page: Page | None = None
        self.session_id: str | None = None
        self._live_url: str | None = None

    @property
    def live_url(self) -> str | None:
        if self._live_url:
            return self._live_url
        if not self.session_id:
            return None
        debug_info = bb.sessions.debug(self.session_id)
        self._live_url = debug_info.debugger_fullscreen_url
        return self._live_url

    def _handle_captcha(self) -> None:
        from app.services.captcha import handle_captcha

        resolved = handle_captcha(
            page=self.page,
            session_url=self.live_url or "",
            subject=f"Captcha on {self.page.url}",
            message=f"A captcha was encountered navigating to:\n{self.page.url}",
        )
        if not resolved:
            raise RuntimeError(f"Captcha not resolved on {self.page.url}")

    def __enter__(self) -> "BrowserSession":
        proxies = None
        if self._proxy_country:
            proxies = [
                {"type": "browserbase", "geolocation": {"country": self._proxy_country}}
            ]

        create_kwargs = {
            "project_id": browserbase_settings.BROWSERBASE_PROJECT_ID,
            "keep_alive": self._keep_alive,
            "region": "ap-southeast-1",
        }
        if proxies:
            create_kwargs["proxies"] = proxies
        if self._context_id:
            create_kwargs["browser_settings"] = {
                "context": {"id": self._context_id, "persist": self._persist_context},
            }

        session = bb.sessions.create(**create_kwargs)
        self.session_id = session.id
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(session.connect_url)
        context = self._browser.contexts[0]
        self.page = context.pages[0]

        # Wrap page.goto so captcha detection runs after every navigation.
        # Callers never need to handle captchas explicitly.
        original_goto = self.page.goto

        def goto_with_captcha(url, **kwargs) -> Response | None:
            kwargs.setdefault("wait_until", "commit")
            response = original_goto(url, **kwargs)
            self._handle_captcha()
            return response

        self.page.goto = goto_with_captcha

        return self

    def detach(self) -> None:
        """Close Playwright connection without releasing the Browserbase session.

        Use this when the deterministic path fails and you want to hand the
        still-alive session to an LLM agent for recovery.
        """
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None

    def release(self) -> None:
        """Release the Browserbase session. Idempotent."""
        if self.session_id:
            log.info("Releasing session %s", self.session_id)
            try:
                bb.sessions.update(self.session_id, status="REQUEST_RELEASE")
            except Exception:
                log.debug(
                    "Failed to release session %s", self.session_id, exc_info=True
                )
            self.session_id = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.detach()
        self.release()
        return False


@stamina.retry(on=Exception, timeout=300)
def authenticate_platform(platform) -> str:
    """Create a context, log in on a platform, and persist auth cookies.

    Returns the context_id. On failure stamina retries with exponential backoff.
    The `platform` argument must satisfy the SupplierPlatform protocol (login method).
    """
    context_id = create_context()
    try:
        with BrowserSession(
            proxy_country="AU",
            context_id=context_id,
            persist_context=True,
        ) as browser:
            session_id = browser.session_id
            platform.login(browser.page, session_url=browser.live_url or "")
    except Exception:
        log.exception("Auth failed for %s, stamina will retry", platform)
        raise
    wait_for_session_complete(session_id)
    return context_id


# ---------------------------------------------------------------------------
# Browse CLI toolkit for LLM agents
# ---------------------------------------------------------------------------

BROWSE_BIN = str(PROJECT_ROOT / "node_modules" / ".bin" / "browse")

_I18N_PATTERN = re.compile(r'"intl-|gangesweb|"i18n')

BROWSE_TOOL_DOCS = """\
## Tools

**screenshot** — capture a screenshot to see the current page.

**browse** — run a CLI command against the browser session:
- click <selector>         — click by CSS selector, text, or snapshot ref. \
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

**IMPORTANT**: Do NOT use `click_xy` for buttons, links, or form elements — \
coordinates from screenshots are unreliable. Use text/CSS selectors or \
snapshot refs instead. Reserve `click_xy` only for captcha sliders or drag \
operations where no selector exists."""


class BrowseToolkit:
    """Browse CLI tools bound to a Browserbase session.

    Provides screenshot and browse command tools as PydanticAI Tools. Each
    agent creates a toolkit instance for its session, calls .tools() for the
    base set, and adds its own task-specific tools on top.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._env = {
            **os.environ,
            "BROWSERBASE_API_KEY": browserbase_settings.BROWSERBASE_API_KEY,
            "BROWSERBASE_PROJECT_ID": browserbase_settings.BROWSERBASE_PROJECT_ID,
        }

    @staticmethod
    def strip_i18n(output: str) -> str:
        """Remove i18n/localisation noise lines from snapshot output."""
        return "\n".join(
            line for line in output.splitlines() if not _I18N_PATTERN.search(line)
        )

    def run_browse(
        self, parts: list[str], timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        """Low-level browse CLI runner. Used by the browse tool and available
        for direct Playwright-style automation in deterministic scripts."""
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
            args = [BROWSE_BIN, "--connect", self.session_id, verb, "--"] + rest
        else:
            args = [BROWSE_BIN, "--connect", self.session_id] + parts
        log.info("browse CLI: %s", " ".join(args[2:]))
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=self._env
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
    def screenshot(self, reason: str) -> BinaryContent | str:
        """Capture a screenshot of the current browser page.

        Args:
            reason: Why you are taking this screenshot (e.g. "checking if modal opened after click").
        """
        log.info("Screenshot reason: %s", reason)
        EVICTION_DIR.mkdir(exist_ok=True)
        fpath = EVICTION_DIR / f"screenshot_{uuid.uuid4().hex[:8]}.png"
        try:
            result = self.run_browse(["screenshot", str(fpath)], timeout=30)
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
    def browse(self, command: str, reason: str) -> str:
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
            result = self.run_browse(parts)
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
            output = self.strip_i18n(output)

        return output

    def tools(self) -> list[Tool]:
        """Return the base [screenshot, browse] tools as PydanticAI Tools."""
        return [
            Tool(self.screenshot, takes_ctx=False),
            Tool(self.browse, takes_ctx=False),
        ]

    def make_finish_tool(
        self,
        status_enum: type[Enum],
        result_model: type[BaseModel],
        holder: list,
    ) -> Tool:
        """Create a finish tool parameterized by the agent's status enum and
        result model. The generated function appends to holder so the caller
        can retrieve the result after the agent run."""

        def finish(status, reason: str = "") -> str:
            """Report the outcome. You MUST call this when done.

            Args:
                status: The outcome status.
                reason: Explanation if failed, empty on success.
            """
            holder.append(result_model(status=status, reason=reason))
            log.info("Agent finished: status=%s reason=%s", status, reason[:200])
            return f"Recorded: {status.value}"

        finish.__annotations__["status"] = status_enum
        return Tool(finish, takes_ctx=False)

    @staticmethod
    def classify_from_history(
        messages: list[ModelMessage],
        system_prompt: str,
        output_type: type[BaseModel],
        name: str = "fallback",
    ) -> Any:
        """Fallback: pass the agent's conversation to a toolless agent with
        structured output to classify the outcome when finish was not called."""
        fallback = _BaseAgent(
            model=get_model(model_settings.MODERATE),
            name=name,
            system_prompt=system_prompt,
            output_type=output_type,
            retries=2,
        )
        result = fallback.run_sync(
            "You did not call finish. Based on your conversation above, "
            "classify the outcome now.",
            message_history=messages,
        )
        log.info("Fallback classification: %s", result.output)
        return result.output
