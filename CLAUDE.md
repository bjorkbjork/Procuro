# Supplier Sourcing Agent

> **All planning and task tracking goes through beads (`bd --help`).** Create issues for work items, track dependencies, and use `bd ready` to find what to work on next. Do not use TodoWrite for multi-step work.

## Project Overview

Autonomous agent that scrapes product specs from retailer websites (Kogan, Kmart, etc.), finds matching suppliers on B2B platforms (GlobalSources, Alibaba), negotiates FOB prices via email, and logs results to Google Sheets.

## Stack

- **Python 3.12**, managed by PDM (`pdm add <pkg>`)
- **PydanticAI** — agent framework (agents, tools, structured output)
- **Claude + multi-model via AWS Bedrock** — LLM for spec extraction, triage, negotiation (Sonnet, Haiku, Opus + Mistral, DeepSeek, Qwen via model pools)
- **Browserbase + Playwright** — cloud browser sessions via Browserbase, Playwright for page automation
- **Gmail API** — inbox polling, reply, archive
- **Google Sheets API** — live results output
- **Postgres** — state persistence (SQLAlchemy, Alembic)
- **Pydantic Logfire** — observability
- **APScheduler** — polling intervals

## Development

- **Config**: All secrets in `.env` (gitignored). See `app/base/config.py` for required vars.
- **Database**: Local Postgres. Run migrations with `alembic upgrade head`.
- **Testing**: TDD when practical — write unit tests first, then implement. No mocks for external services; each pipeline stage is tested as an isolated script against real services. Iterate using Browserbase dashboard for browser debugging. Tests hitting real services are marked `@pytest.mark.integration` and excluded from default `pdm run pytest`. Run them with `pdm run pytest -m integration`.

## Conventions

- Sync SQLAlchemy only (no async engine).
- State machine states are defined in `supplier_thread.py:VALID_STATES`.
- All browser automation goes through Browserbase, never local Playwright.
- Commit messages: `Type: Short description` (e.g. `Feat:`, `Fix:`, `Chore:`).
