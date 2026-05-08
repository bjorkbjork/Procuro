# Application Architecture

This document explains the core abstractions that pipeline stages are built on.

## LLM Client — `app/base/llm.py`

All LLM calls go through a `RotatingModel` that wraps AWS Bedrock. Each model tier (cheap, moderate, expensive, browser, match) has a **pool** of models with per-model RPM limits defined in `config.py`. When a model returns 429, `RotatingModel` transparently switches to the next model in the pool and retries. Agent code never sees the rotation — it writes to one logical model and the pool handles the rest.

Key components:

- **`RotatingModel`** — subclass of `BedrockConverseModel`. Tracks per-model RPM usage with sliding 60-second windows. `_pick_model()` selects an available model; if all are exhausted it sleeps until one frees up.
- **`Agent`** — subclass of `pydantic_ai.Agent`. Auto-injects tool-result eviction: when a tool return exceeds 5% of the context window (~10k tokens), it's written to a `snapshots/` file and replaced with a placeholder. The agent gets `grep_evicted_result` and `read_evicted_result` tools to explore evicted content on demand.
- **`get_model(model_id, pool)`** — returns a shared `RotatingModel` for a given tier. RPM state is shared across all callers within the process.
- **Image handling** — non-Anthropic models (Qwen, Mistral, etc.) reject images inside `toolResult` blocks on Bedrock Converse. `RotatingModel` rewrites image tool returns into top-level content blocks before sending to those models.

## Batch Executor — `app/pipeline/batch_executor.py`

Generic threading framework for parallel batch processing. Subclasses define:

- `get_work_items() -> dict[str, list[T]]` — return items grouped by key (e.g. platform name)
- `_process_batch(batch, group_key)` — process one batch sequentially in a worker thread
- `stage` / `action` — identifiers for logging and event recording

`execute()` fans out batches to a `ThreadPoolExecutor` (capped at `MAX_WORKERS`). Items within a batch run sequentially; different groups run in parallel.

## Browser Fallback Executor — `app/pipeline/browser_executor.py`

Subclass of `BatchExecutor` that implements the **deterministic → LLM agent fallback** pattern for all browser automation stages. Each batch authenticates once per platform, then processes items:

1. **Deterministic path** — fast Playwright automation via `deterministic_action()`. Cheap and reliable for the happy path.
2. **Agent fallback** — on failure, the Playwright connection detaches but the Browserbase session stays alive. An LLM agent (`agent_fallback()`) takes over the same browser session and attempts recovery.
3. **Re-auth loop** — if the agent reports `login_required`, the executor re-authenticates and retries affected items (up to `REAUTH_MAX_RETRIES`).

Every attempt (success, fallback, or failure) is recorded to the `automation_events` table. `check_automation_failure_rate()` scans recent events and emails the maintainer if any (stage, action) pair exceeds the failure threshold.

Subclasses implement:

- `deterministic_action(item, page, platform, context_id)` — Playwright flow
- `agent_fallback(item, session_id, platform)` — LLM recovery
- `on_success(item, result)` — post-success bookkeeping (DB updates, etc.)

A one-shot helper `run_with_browser_fallback()` provides the same lifecycle for stages that process individual items rather than batches (e.g. stage 5 negotiation replies).

### Example: OutreachExecutor (`s3_outreach.py`)

```
OutreachExecutor(BrowserFallbackExecutor)
  ├─ get_work_items()        → NEW threads grouped by platform
  ├─ deterministic_action()  → platform.send_inquiry(page, url, message)
  ├─ agent_fallback()        → send_inquiry_via_agent(session_id, ...)
  └─ on_success()            → thread.state = OUTREACH_SENT + insert Message
```

## Browser Sessions — `app/services/browser.py`

Manages Playwright-over-Browserbase with geo-proxied sessions and persistent auth contexts.

- **`BrowserSession`** — context manager. Creates a Browserbase session with optional geo-proxy and persistent context (auth cookies). Wraps `page.goto()` with automatic captcha detection. Supports `detach()` (close Playwright, keep session alive for agent handoff) and `release()`.
- **`BrowseToolkit`** — provides `screenshot` and `browse` as PydanticAI Tools for LLM agents. Runs the `browse` CLI (Node.js subprocess) connected to a live Browserbase session. Includes `make_finish_tool()` for structured agent termination and `classify_from_history()` as a fallback if the agent doesn't call finish.
- **`authenticate_platform(platform)`** — creates a persistent Browserbase context, runs `platform.login()`, and returns the context ID. Retried with exponential backoff via stamina.

## Database — `app/db/database.py`

Sync SQLAlchemy with connection pooling (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`). All stages use `with SessionLocal() as session:` for scoped transactions.

## Scheduler — `app/base/scheduler.py`

APScheduler `BlockingScheduler` with an `SQLAlchemyJobStore` (jobs persisted in the same Postgres database). `coalesce=True` prevents stacked invocations on missed windows.

Jobs registered in `app/main.py`:

| Job | Interval | Notes |
|-----|----------|-------|
| Sourcing pipeline | Every 15 min | Stages 1→2→3→6 |
| Negotiation pipeline | Every 30 min | Stages 4→5→6, business hours only (Mon–Fri 7am–6pm AEST) |
| Recovery | Every 15 min | Re-runs stalled matching, search, or outreach |
| Reporting sync | Every 30 min | Syncs dashboard, active threads, pipeline, activity tabs |
