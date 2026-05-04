"""LLM fallback agent for reading and replying to on-platform supplier messages.

Same role as inquiry_agent — the deterministic Playwright path in each
platform's messaging.py runs first, and this agent recovers failures.
Uses BrowseToolkit from the browser service for browse CLI tools.

Two entry points:
  read_inbox_via_agent  — reads unread conversations, returns structured data
  send_reply_via_agent  — sends a reply in a specific conversation
"""

import logging
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.base.config import model_settings
from app.base.llm import Agent, get_model
from app.services.browser import BROWSE_TOOL_DOCS, BrowseToolkit

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inbox reader
# ---------------------------------------------------------------------------

INBOX_SYSTEM_PROMPT = f"""\
You are a browser automation recovery agent. A coded automation flow tried \
to read unread messages from a supplier messaging platform but failed. You \
have been dropped into the exact browser session where it got stuck.

Your job: find and report all unread supplier messages.

{BROWSE_TOOL_DOCS}

## Workflow

1. Take a screenshot to understand the current page state.
2. Identify the conversation list and look for unread conversations \
   (bold text, badge, dot, or similar indicator).
3. For each unread conversation:
   a. Click into the conversation.
   b. Read the latest message(s) from the supplier — use screenshot \
      and/or `get text` to capture the full message content.
   c. Identify the supplier/company name from the conversation header.
   d. Use `get url` to capture the conversation URL.
   e. Call `report_message` with the supplier name, message text, and URL.
   f. Navigate back to the conversation list for more.
4. When all unread conversations have been reported (or there are none), \
   call `finish`.

## Finishing

Call `finish` with the outcome:
- **SUCCESS** — inbox was read (even if no unread messages found).
- **LOGIN_REQUIRED** — page shows a login/sign-in form. Do NOT attempt \
  to log in — call finish immediately.
- **FAILED** — could not read the inbox. Include a reason.

## Rules

- Report ALL unread conversations, not just the first one.
- Include the full message text, not just a summary.
- Do NOT reply to any messages — only read and report them."""


class InboxStatus(str, Enum):
    SUCCESS = "SUCCESS"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    FAILED = "FAILED"


class InboxMessage(BaseModel):
    supplier_name: str = Field(description="Company or contact name")
    message_text: str = Field(description="Full text of the latest unread message")
    conversation_url: str = Field(description="URL of the conversation page")


class InboxReadResult(BaseModel):
    status: InboxStatus = Field(description="Outcome of the inbox read attempt")
    reason: str = Field(
        default="",
        description="Explanation if failed or login required",
    )
    messages: list[InboxMessage] = Field(default_factory=list)


def read_inbox_via_agent(
    session_id: str, *, platform_prompt: str = ""
) -> InboxReadResult:
    """Read unread messages from a platform inbox using an LLM agent."""
    result_holder: list[InboxReadResult] = []
    collected_messages: list[InboxMessage] = []
    toolkit = BrowseToolkit(session_id)

    def report_message(
        supplier_name: str, message_text: str, conversation_url: str
    ) -> str:
        """Report an unread message found in the inbox. Call this for each
        unread conversation before calling finish.

        Args:
            supplier_name: The supplier/company name shown in the conversation.
            message_text: The full text of the latest unread message from the supplier.
            conversation_url: The URL of the conversation page (from `get url`).
        """
        collected_messages.append(
            InboxMessage(
                supplier_name=supplier_name,
                message_text=message_text,
                conversation_url=conversation_url,
            )
        )
        log.info("Reported platform message from: %s", supplier_name)
        return f"Recorded message from {supplier_name}"

    system_prompt = INBOX_SYSTEM_PROMPT
    if platform_prompt:
        system_prompt = f"{INBOX_SYSTEM_PROMPT}\n\n{platform_prompt}"

    tools = toolkit.tools() + [
        Tool(report_message, takes_ctx=False),
        toolkit.make_finish_tool(InboxStatus, InboxReadResult, result_holder),
    ]

    agent = Agent(
        model=get_model("browser", pool=model_settings.BROWSER_POOL),
        name="platform_inbox_reader",
        system_prompt=system_prompt,
        tools=tools,
        retries=5,
        model_settings={"thinking": "high"},
    )

    run_result = agent.run_sync(
        "The coded automation failed to read the platform inbox. "
        "Start with a screenshot to see where the browser is stuck."
    )

    if not result_holder:
        log.warning("Inbox reader did not call finish — running classification")
        result = BrowseToolkit.classify_from_history(
            run_result.all_messages(),
            system_prompt,
            InboxReadResult,
            "inbox_reader_fallback",
        )
        result.messages = list(collected_messages)
        return result

    result = result_holder[-1]
    result.messages = list(collected_messages)
    return result


# ---------------------------------------------------------------------------
# Reply sender
# ---------------------------------------------------------------------------

REPLY_SYSTEM_PROMPT = f"""\
You are a browser automation recovery agent. A coded automation flow tried \
to send a reply in a supplier messaging conversation but failed. You have \
been dropped into the exact browser session where it got stuck.

Your job: send the reply message in the conversation.

{BROWSE_TOOL_DOCS}

## Workflow

1. Take a screenshot to see the current conversation state.
2. Find the message input area (textarea, input, or contenteditable element).
3. Type or fill the reply message EXACTLY as provided — do not modify it.
4. Find and click the Send button.
5. Take a screenshot to verify the message appeared in the conversation.
6. Call `finish` with the result.

## Finishing

Call `finish` with the outcome:
- **SENT** — message was sent (visible in the conversation).
- **LOGIN_REQUIRED** — page shows a login form. Do NOT attempt to log in.
- **FAILED** — could not send the message. Include a reason.

## Rules

- Do NOT modify the reply message — send it exactly as provided.
- Verify the message was sent before reporting SENT.
- If you cannot find the input area after 3 attempts, call finish with FAILED."""


class ReplyStatus(str, Enum):
    SENT = "SENT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    FAILED = "FAILED"


class ReplyResult(BaseModel):
    status: ReplyStatus = Field(description="Outcome of the reply attempt")
    reason: str = Field(
        default="",
        description="Explanation if failed or login required",
    )


def send_reply_via_agent(
    session_id: str,
    conversation_url: str,
    message: str,
    *,
    platform_prompt: str = "",
) -> ReplyResult:
    """Send a reply in a platform conversation using an LLM agent."""
    result_holder: list[ReplyResult] = []
    toolkit = BrowseToolkit(session_id)

    system_prompt = REPLY_SYSTEM_PROMPT
    if platform_prompt:
        system_prompt = f"{REPLY_SYSTEM_PROMPT}\n\n{platform_prompt}"

    tools = toolkit.tools() + [
        toolkit.make_finish_tool(ReplyStatus, ReplyResult, result_holder),
    ]

    agent = Agent(
        model=get_model("browser", pool=model_settings.BROWSER_POOL),
        name="platform_reply_sender",
        system_prompt=system_prompt,
        tools=tools,
        retries=5,
        model_settings={"thinking": "high"},
    )

    prompt = (
        f"The coded automation failed to send a reply in this conversation.\n\n"
        f"Conversation URL: {conversation_url}\n\n"
        f"Message to send (copy exactly, do not modify):\n"
        f"---\n{message}\n---\n\n"
        f"Start with a screenshot to see where the browser is stuck."
    )

    run_result = agent.run_sync(prompt)

    if not result_holder:
        log.warning("Reply agent did not call finish — running classification")
        return BrowseToolkit.classify_from_history(
            run_result.all_messages(),
            system_prompt,
            ReplyResult,
            "reply_sender_fallback",
        )

    return result_holder[-1]
