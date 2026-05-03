"""Shared Bedrock provider, model factories, and Agent subclass for all agents.

The ``Agent`` exported here wraps PydanticAI's Agent with automatic tool-return
eviction: large results are written to local files and replaced with a
placeholder. Two tools — ``grep_evicted_result`` and ``read_evicted_result`` —
are injected so every agent can explore evicted content."""

import json
import logging
import re
import uuid as uuid_mod
from pathlib import Path
from typing import Any

import boto3
from pydantic_ai.messages import BinaryContent
from botocore.config import Config
from pydantic_ai import Agent as _BaseAgent, ModelRetry, Tool
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

from app.base.config import PROJECT_ROOT, model_settings, settings

log = logging.getLogger(__name__)

_provider = None

EVICTION_DIR = PROJECT_ROOT / "snapshots"
CHARS_PER_TOKEN = 4
EVICTION_MIN_TOKENS = 1_000
EVICTION_BUDGET_RATIO = 0.05


# FIXME: Put AWS Bedrock Guardrail IDs here once deployed
def bedrock_provider() -> BedrockProvider:
    global _provider
    if _provider is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.BEDROCK_REGION,
            config=Config(
                retries={"max_attempts": 10, "mode": "adaptive"},
                read_timeout=120,
                connect_timeout=30,
            ),
        )
        _provider = BedrockProvider(bedrock_client=client)
    return _provider


def get_model(model_id: str = model_settings.MODERATE) -> BedrockConverseModel:
    return BedrockConverseModel(model_name=model_id, provider=bedrock_provider())


# ---------------------------------------------------------------------------
# Context windows per model family (Bedrock model IDs)
# ---------------------------------------------------------------------------

_CONTEXT_WINDOWS: dict[str, int] = {
    "haiku": 200_000,
    "sonnet": 200_000,
    "opus": 200_000,
}


def _get_context_window(model_id: str) -> int:
    for fragment, window in _CONTEXT_WINDOWS.items():
        if fragment in model_id.lower():
            return window
    return 200_000


# ---------------------------------------------------------------------------
# Tool-return eviction
# ---------------------------------------------------------------------------


def _safe_blob_name(tool_call_id: str | None) -> str:
    raw = tool_call_id or str(uuid_mod.uuid4())
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


def _build_placeholder(
    tool_name: str,
    part_tokens: int,
    blob_id: str,
    content: str,
) -> str:
    total_lines = len(content.splitlines())
    return (
        f"Tool result too large for context (~{part_tokens} tokens, {total_lines} lines). "
        f"Full result saved to disk.\n\n"
        f"Blob ID: {blob_id}\n\n"
        f"Use these tools to explore:\n"
        f'  grep_evicted_result(blob_id="{blob_id}", pattern="your_regex")\n'
        f'  read_evicted_result(blob_id="{blob_id}", start_line=1, end_line=100)'
    )


def _estimate_tokens(messages: list[ModelMessage]) -> int:
    total = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    c = (
                        part.content
                        if isinstance(part.content, str)
                        else json.dumps(part.content, default=str)
                    )
                    total += len(c) // CHARS_PER_TOKEN
    return total


def _evict_oversized_tool_returns(
    messages: list[ModelMessage],
    context_window: int,
    evicted_ids: set[str],
) -> list[ModelMessage]:
    """Replace tool returns that would overflow the context with a placeholder
    and write the full content to a local file.

    Mutates ToolReturnPart.content in-place.
    evicted_ids is in/out — already-evicted IDs are skipped, new ones added.
    """
    budget = int(context_window * EVICTION_BUDGET_RATIO)
    EVICTION_DIR.mkdir(exist_ok=True)

    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_call_id and part.tool_call_id in evicted_ids:
                continue

            # Binary content (screenshots, PDFs, etc.) — leave in context
            if isinstance(part.content, BinaryContent):
                continue

            content_str = (
                part.content
                if isinstance(part.content, str)
                else json.dumps(part.content, default=str)
            )
            part_tokens = len(content_str) // CHARS_PER_TOKEN
            if part_tokens <= EVICTION_MIN_TOKENS:
                continue
            if part_tokens <= budget:
                continue

            blob_id = _safe_blob_name(part.tool_call_id)
            log.warning(
                "Evicting oversized tool return from %s "
                "(~%d tokens, budget: %d) → %s",
                part.tool_name,
                part_tokens,
                budget,
                blob_id,
            )

            fpath = EVICTION_DIR / f"{blob_id}.txt"
            fpath.write_text(content_str)

            part.content = _build_placeholder(
                part.tool_name,
                part_tokens,
                blob_id,
                content_str,
            )
            if part.tool_call_id:
                evicted_ids.add(part.tool_call_id)

    return messages


# ---------------------------------------------------------------------------
# Eviction retrieval tools
# ---------------------------------------------------------------------------


def grep_evicted_result(blob_id: str, pattern: str, context: int = 0) -> str:
    """Search an evicted tool result for lines matching a regex pattern.

    Works like grep -n (or grep -C when context > 0).
    Returns matching lines with line numbers.
    """
    fpath = EVICTION_DIR / f"{blob_id}.txt"
    if not fpath.exists():
        raise ModelRetry(f"No evicted result found for blob_id '{blob_id}'")
    try:
        lines = fpath.read_text().splitlines()
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ModelRetry(f"Invalid regex pattern: {e}")

    if context <= 0:
        output = [
            f"{i + 1}:{line}" for i, line in enumerate(lines) if compiled.search(line)
        ]
    else:
        match_indices = [i for i, line in enumerate(lines) if compiled.search(line)]
        output: list[str] = []
        prev_end = -1
        for idx in match_indices:
            ctx_start = max(0, idx - context)
            ctx_end = min(len(lines), idx + context + 1)
            if prev_end >= 0 and ctx_start > prev_end:
                output.append("--")
            start = max(ctx_start, prev_end) if prev_end > ctx_start else ctx_start
            for j in range(start, ctx_end):
                output.append(f"{j + 1}:{lines[j]}")
            prev_end = ctx_end

    if not output:
        return f"No matches for '{pattern}' in {len(lines)} lines."
    return "\n".join(output[:200])


def read_evicted_result(blob_id: str, start_line: int, end_line: int) -> str:
    """Read a line range from an evicted tool result. Lines are 1-indexed."""
    fpath = EVICTION_DIR / f"{blob_id}.txt"
    if not fpath.exists():
        raise ModelRetry(f"No evicted result found for blob_id '{blob_id}'")
    lines = fpath.read_text().splitlines()
    start = max(0, start_line - 1)
    end = min(len(lines), end_line)
    chunk = lines[start:end]
    return f"Lines {start + 1}-{end} of {len(lines)} total:\n" + "\n".join(chunk)


_BINARY_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _resolve_evicted_file(blob_id: str) -> Path:
    """Find the evicted file for a blob_id, regardless of extension."""
    for fpath in EVICTION_DIR.glob(f"{blob_id}.*"):
        return fpath
    raise ModelRetry(f"No evicted result found for blob_id '{blob_id}'")


def read_evicted_file(blob_id: str) -> BinaryContent | str:
    """Read an evicted binary file (screenshot, PDF, etc.) and return it to the model."""
    fpath = _resolve_evicted_file(blob_id)
    media_type = _BINARY_MEDIA_TYPES.get(fpath.suffix, "")
    if media_type:
        return BinaryContent(data=fpath.read_bytes(), media_type=media_type)
    return fpath.read_text()


_EVICTION_TOOLS = [
    Tool(grep_evicted_result, takes_ctx=False),
    Tool(read_evicted_result, takes_ctx=False),
]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_evicted(evicted_ids: set[str]) -> None:
    """Delete all evicted files for a set of blob IDs."""
    if not EVICTION_DIR.exists():
        return
    for blob_id in evicted_ids:
        for fpath in EVICTION_DIR.glob(f"{blob_id}.*"):
            fpath.unlink(missing_ok=True)
            log.debug("Cleaned up evicted file: %s", fpath.name)


# ---------------------------------------------------------------------------
# Agent subclass with eviction baked in
# ---------------------------------------------------------------------------


class Agent(_BaseAgent):
    """PydanticAI Agent with automatic tool-return eviction.

    Large tool results are written to local files and replaced with
    placeholders. The agent automatically gets grep/read tools to
    explore evicted content.
    """

    _evicted_ids: set[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        model = kwargs.get("model") or (args[0] if args else None)
        model_id = getattr(model, "model_name", "") if model else ""
        context_window = _get_context_window(model_id)
        evicted_ids: set[str] = set()

        def eviction_processor(messages: list[ModelMessage]) -> list[ModelMessage]:
            return _evict_oversized_tool_returns(messages, context_window, evicted_ids)

        existing_processors = list(kwargs.get("history_processors") or [])
        existing_processors.insert(0, eviction_processor)
        kwargs["history_processors"] = existing_processors

        existing_tools = list(kwargs.get("tools") or [])
        existing_tools.extend(_EVICTION_TOOLS)
        kwargs["tools"] = existing_tools

        super().__init__(*args, **kwargs)
        self._evicted_ids = evicted_ids

    def run_sync(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().run_sync(*args, **kwargs)
        finally:
            cleanup_evicted(self._evicted_ids)
