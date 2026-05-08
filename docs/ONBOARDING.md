# Onboarding Guide — Supplier Sourcing Agent

## Project Overview

An autonomous agent that sources supplier quotes for Kogan products from GlobalSources and Alibaba, negotiates FOB prices via email, and logs results to Google Sheets.

| | |
|---|---|
| **Languages** | Python, JavaScript, YAML, Dockerfile, Shell |
| **Frameworks** | PydanticAI, SQLAlchemy, Alembic, Playwright, Browserbase, APScheduler, Pydantic Logfire, pytest |
| **Infrastructure** | Docker, Docker Compose, GitHub Actions, AWS Bedrock, Postgres 16 |

---

## Architecture Layers

### 1. Foundation

Shared configuration (Pydantic Settings), LLM model pool setup (AWS Bedrock multi-model rotation), and APScheduler scheduling primitives used across the entire application.

| File | Purpose |
|---|---|
| `app/base/config.py` | Central config via Pydantic Settings — loads all env vars for Gmail, Bedrock, Postgres, Sheets, Browserbase, scheduler |
| `app/base/llm.py` | RotatingModel distributing requests across a pool of Bedrock models with per-model RPM limits and 429 failover |
| `app/base/scheduler.py` | Blocking APScheduler backed by SQLAlchemy job store |
| `app/main.py` | Entry point — registers cron jobs for sourcing, negotiation, stalled-thread recovery, and reporting |

### 2. Data Layer

SQLAlchemy ORM models for the full sourcing lifecycle, plus Postgres session management and Alembic migrations.

| Model | Role |
|---|---|
| `SourceProduct` | Kogan product specs extracted in stage 1 |
| `SupplierProduct` | Discovered supplier listings with LLM match scoring |
| `SupplierThread` | Central state machine (NEW → OUTREACH_SENT → AWAITING_REPLY → NEGOTIATING → FINAL_PRICE_LOGGED) |
| `Quote` | Recorded supplier price quotes (FOB, MOQ, lead time, currency) |
| `Message` | Email and platform messages within supplier threads |
| `AutomationEvent` | Pipeline stage outcome logging |
| `KeyValue` | Generic JSONB key-value store for app state |

### 3. Services Layer

External service integrations — browser automation, Gmail, Sheets, OAuth, CAPTCHA, and platform adapters.

| Service | Role |
|---|---|
| `app/services/browser.py` | Browserbase session management + LLM browse toolkit (BrowseToolkit generates PydanticAI tools) |
| `app/services/gmail.py` | Gmail API client — list threads, read, send, reply, archive |
| `app/services/sheets.py` | Google Sheets API client managing input/output across 8 tabs |
| `app/services/google_auth.py` | Multi-tier OAuth: DB token → .env token → automated browser login with TOTP 2FA |
| `app/services/captcha/` | Multi-type captcha detection and resolution (hCaptcha, reCAPTCHA, Cloudflare, slider) |
| `app/services/platforms/alibaba/` | Alibaba adapter — internal JSON API search, Playwright login/inquiry, messaging |
| `app/services/platforms/globalsources/` | GlobalSources adapter — API search, Google SSO login, inquiry, spec parsing |
| `app/services/sources/kogan/` | Kogan product page parser (BeautifulSoup) |

### 4. Pipeline Layer

Six-stage sourcing pipeline with PydanticAI agents for LLM-driven decisions.

| Stage | File | What it does |
|---|---|---|
| S1 — Spec Extraction | `s1_spec_extraction.py` | Fetches Kogan pages via Browserbase, parses titles/specs, upserts SourceProduct |
| S2 — Supplier Search | `s2_supplier_search.py` | LLM-generated queries → platform search → spec fetch → manufacturer filter → LLM matching |
| S3 — Outreach | `s3_outreach.py` | Sends inquiry messages via browser automation with deterministic-then-agent fallback |
| S4 — Inbox Triage | `s4_inbox_triage.py` | Polls Gmail + platform messages, classifies via LLM triage, fuzzy-matches to threads |
| S5 — Negotiation | `s5_negotiation.py` | LLM-driven negotiation — counter-offers, quote extraction, reply via Gmail or platform |
| S6 — Sheet Update | `s6_sheet_update.py` | Syncs results to 8 Google Sheets tabs with dashboard analytics |

**Agents** powering the pipeline:

| Agent | Role |
|---|---|
| `query_agent.py` | Generates diverse search queries from product specs |
| `match_agent.py` | Compares reference product vs. supplier candidates for spec matching |
| `inquiry_agent.py` | Submits inquiry forms via browser automation |
| `negotiation_agent.py` | Handles price negotiation, quote extraction, counter-offers |
| `platform_message_agent.py` | Reads/sends messages on platform messaging systems |

### 5. Infrastructure & CI/CD

| File | Role |
|---|---|
| `Dockerfile` | Python 3.12-slim + uv + Node.js for JS browser scripts |
| `docker-compose.yml` | App + Postgres 16 with health checks |
| `.github/workflows/build-deploy.yml` | CI: pytest → Docker build → GHCR push → SSH deploy with 30 injected secrets |
| `scripts/create_release.sh` | Semantic version tagging to trigger deployments |

### 6. Tests

Unit and integration tests with shared pytest fixtures in `conftest.py` using in-memory SQLite (no real DB server needed for unit tests). Live-service tests are marked `@pytest.mark.integration` and excluded from default runs.

### 7. Development Scripts

Standalone scripts in `scripts/` for manual pipeline execution (`run_pipeline.py`) and interactive exploration of platform flows (`explore_gs_inquiry.py`, `explore_gs_messages.py`, `watch_inquiry.py`).

---

## Key Concepts

- **Browser Fallback Executor** — Two-tier pattern used across stages: try deterministic Playwright automation first, fall back to an LLM agent if page structure has changed (`app/pipeline/browser_executor.py`).
- **RotatingModel** — Custom PydanticAI Model that wraps multiple BedrockConverseModel instances with sliding-window RPM tracking and automatic load balancing.
- **SupplierPlatform Protocol** — Contract that Alibaba and GlobalSources implementations satisfy (search, parse, login, inquiry, messaging). Platforms are auto-discovered at runtime via `pkgutil`.
- **MarketplaceSource Protocol** — Abstraction for product sources (currently Kogan), making it easy to add new product sources.
- **State Machine** — `SupplierThread` drives the lifecycle: NEW → OUTREACH_SENT → AWAITING_REPLY → NEGOTIATING → FINAL_PRICE_LOGGED (plus `unprocessable` for dead ends).
- **No async** — Concurrency is handled via `ThreadPoolExecutor` and APScheduler, not async/await.
- **All browser sessions are cloud** — Everything runs through Browserbase, never local Playwright.

---

## Guided Tour

Follow this path to understand the codebase end-to-end:

### Step 1: Project Overview
Read `README.md` and `SPEC.md` to understand the project's purpose and the full 6-stage pipeline architecture.

### Step 2: Application Entry Point
`app/main.py` orchestrates everything — registers APScheduler cron jobs, chains stages 1–3 (sourcing pipeline) and 4–6 (negotiation pipeline). `ThreadPoolExecutor` provides fan-out concurrency with a reentrant lock preventing overlapping runs.

### Step 3: Configuration and LLM Layer
`app/base/config.py` loads all environment variables via Pydantic Settings. `app/base/llm.py` builds the RotatingModel that distributes LLM requests across AWS Bedrock models with per-model RPM limits and automatic 429 failover.

### Step 4: Database Models
Understand the data model: `SourceProduct` → `SupplierProduct` → `SupplierThread` (state machine) → `Quote` + `Message`. These track the full sourcing lifecycle from product spec to final negotiated price.

### Step 5: Stage 1 — Spec Extraction
`s1_spec_extraction.py` fetches Kogan product pages via Browserbase, parses with BeautifulSoup, and upserts `SourceProduct` records. The `MarketplaceSource` protocol makes adding new sources straightforward.

### Step 6: Stage 2 — Supplier Search
The most complex stage. LLM-generated queries → platform search → concurrent spec fetching → manufacturer filtering → LLM-based matching. Multi-round cycles until match thresholds or candidate limits are reached.

### Step 7: Platform Abstraction
`app/services/platforms/platform.py` defines the `SupplierPlatform` protocol. Alibaba and GlobalSources are subpackages auto-discovered at runtime. Both delegate authentication through Google SSO flows automated via Browserbase.

### Step 8: Browser Automation Layer
`app/services/browser.py` wraps Browserbase cloud sessions with geo-proxy support and persistent context. `BrowseToolkit` generates PydanticAI tools that let LLM agents control browser sessions. `BrowserFallbackExecutor` implements the deterministic-then-agent pattern.

### Step 9: Stage 3 — Outreach
Sends initial inquiry messages via the `OutreachExecutor` (extends `BrowserFallbackExecutor`). Deterministic Playwright form filling first, LLM inquiry agent as fallback. Transitions threads from NEW to OUTREACH_SENT.

### Step 10: Stage 4 — Inbox Triage
Polls Gmail and platform message centers. Emails go through ignore list → no-reply detection → platform notification matching → LLM triage. Platform messages are fuzzy-matched to existing supplier threads.

### Step 11: Stage 5 — Negotiation
Processes threads with new inbound messages. Spec-match validation → negotiation agent (full message history) → decide: reply, stay silent, or close. Quotes extracted, replies sent, threads progress toward FINAL_PRICE_LOGGED.

### Step 12: Stage 6 — Reporting
Syncs results to Google Sheets across 8 tabs with funnel conversion rates, per-product metrics, channel distribution, response timing, and automation event summaries.

### Step 13: External Service Integration
Google OAuth is the most complex integration — multi-tier token refresh with automated browser login via Browserbase including TOTP 2FA handling. Powers both Gmail and Sheets services.

### Step 14: Containerization and CI/CD
Dockerfile packages the app with uv + Node.js. Docker Compose orchestrates with Postgres 16. GitHub Actions CI runs test → build → deploy with SSH and 30 injected secrets. `scripts/create_release.sh` automates semantic version tagging.

---

## Complexity Hotspots

These files have the highest complexity — approach them carefully and read the related tests before making changes:

| File | Why it's complex |
|---|---|
| `app/main.py` | Orchestrates entire pipeline — cron jobs, fan-out, locking, error handling |
| `app/base/llm.py` | Multi-model rotation, RPM tracking, 429 failover, exponential backoff |
| `app/services/browser.py` | Browserbase session management, proxy config, LLM browse toolkit generation |
| `app/services/google_auth.py` | Multi-tier OAuth with automated browser login and TOTP 2FA |
| `app/services/captcha/service.py` | Multi-type captcha detection and resolution |
| `app/services/sheets.py` | 8-tab spreadsheet management with upsert semantics |
| `app/services/platforms/alibaba/service.py` | Search API, login, inquiry, spec parsing — full platform adapter |
| `app/services/platforms/alibaba/messaging.py` | Deterministic Playwright automation for Alibaba message center |
| `app/services/platforms/globalsources/service.py` | Full GlobalSources platform adapter |
| `app/pipeline/browser_executor.py` | Deterministic-then-agent fallback framework |
| `app/pipeline/agents/inquiry_agent.py` | Browser-driven form submission agent |
| `app/pipeline/agents/negotiation_agent.py` | LLM negotiation with quote extraction and counter-offers |
| `app/pipeline/agents/platform_message_agent.py` | Platform messaging read/send agents |
| `app/pipeline/stages/s2_supplier_search.py` | Multi-round search-match cycles with concurrent spec fetching |
| `app/pipeline/stages/s3_outreach.py` | Browser automation with fallback for inquiry submission |
| `app/pipeline/stages/s4_inbox_triage.py` | Multi-source inbox polling with LLM classification |
| `app/pipeline/stages/s5_negotiation.py` | Full negotiation loop with spec validation and reply sending |
| `app/pipeline/stages/s6_sheet_update.py` | 8-tab sync with computed dashboard analytics |
| `conftest.py` | In-memory SQLite fake session replacing real DB for all unit tests |

---

## Getting Started

1. Copy `.env.example` to `.env` and fill in all required variables (see `app/base/config.py` for the full list)
2. Start Postgres: `docker compose up -d db`
3. Run migrations: `alembic upgrade head`
4. Run unit tests: `pdm run pytest` (excludes integration tests by default)
5. Run a specific pipeline stage: `pdm run python scripts/run_pipeline.py <stage>`
6. Full application: `docker compose up`
