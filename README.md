# Supplier Sourcing Agent

Autonomous agent that sources supplier quotes for Kogan products from GlobalSources and Alibaba, negotiates FOB prices via email, and logs results to Google Sheets.

See `SPEC.md` for the full technical specification.

## Prerequisites

### Docker

[Docker](https://docs.docker.com/get-docker/) and Docker Compose are required to run the system locally.

### AWS Bedrock

An AWS account with Bedrock model access enabled for the required models in your chosen region (Claude Sonnet/Haiku/Opus, plus Mistral, DeepSeek, Qwen for rate-limit pools). You'll need an IAM user with `bedrock:InvokeModel` permissions.

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `BEDROCK_REGION` (default: `ap-southeast-2`)

### Google Cloud OAuth

A Google Cloud project with the Gmail API and Google Sheets API enabled. Create an OAuth 2.0 Client ID (Desktop app type).

Follow the [Google OAuth 2.0 guide](https://developers.google.com/identity/protocols/oauth2) to set up credentials.

Required env vars from the OAuth client JSON:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_PROJECT_ID`
- `GOOGLE_AUTH_URI`
- `GOOGLE_TOKEN_URI`
- `GOOGLE_AUTH_PROVIDER_X509_CERT_URL`
- `GOOGLE_SHEET_ID` — the spreadsheet containing Input and Output tabs

The agent authenticates itself via Browserbase using its own Gmail credentials. On first run it will perform the OAuth consent flow automatically and store the refresh token in Postgres.

- `GMAIL_ACCOUNT` — the agent's Gmail address
- `GMAIL_PASSWORD` — the agent's Gmail password (app password if 2FA enabled)

### Browserbase

A [Browserbase](https://www.browserbase.com/) account for cloud browser sessions. Residential proxies are used for AU geolocation to bypass bot detection.

- `BROWSERBASE_API_KEY`
- `BROWSERBASE_PROJECT_ID`

## Setup

Copy `.env.example` to `.env` and fill in all required values (see `app/base/config.py` for the full list).

## Running locally

```bash
docker compose up --build
```

This starts Postgres and the app container. The entrypoint runs `alembic upgrade head` automatically before launching the scheduler.

To run without rebuilding (pulls the latest published image):

```bash
docker compose up
```

## Testing

Install dev dependencies with [PDM](https://pdm-project.org/): `pdm install`.

```bash
pdm run pytest
```

## Exploring the Architecture

For a detailed walkthrough of the architecture, pipeline stages, and complexity hotspots, see [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

An interactive knowledge graph is also included, built with [Understand Anything](https://github.com/Lum1104/Understand-Anything). It visualizes every file, module, and dependency as a navigable graph — making it easy to see how the pipeline stages, platform adapters, and LLM agents connect. A 14-step guided tour walks through the system end-to-end, from entry point to CI/CD.

To launch the dashboard locally in Claude Code:

```
/plugin marketplace add Lum1104/Understand-Anything
/plugin install understand-anything
/reload-plugins
/understand-dashboard
```

## Deployment

The deploy workflow provisions and deploys to any Ubuntu LTS server via SSH. It is tag-based: only the latest git tag is deployed, so you can commit freely and deploy when ready.

### 1. Create a GitHub Environment

Go to **Settings > Environments** in your GitHub repo and create an environment (e.g. `production`).

Add the following secrets to the environment:

| Secret | Description |
|---|---|
| `DEPLOY_HOST` | Server hostname or IP (e.g. `ec2-1-2-3-4.ap-southeast-2.compute.amazonaws.com`) |
| `DEPLOY_USER` | SSH username (e.g. `ubuntu` for Ubuntu LTS) |
| `DEPLOY_SSH_KEY` | Full contents of your SSH private key (`.pem` file) |

### 2. Add application secrets

Add these required secrets to the same environment. See `app/base/config.py` for the full list of supported variables. Only secrets you set are written to the server's `.env` -- anything omitted uses the Pydantic default from `config.py`.

**Required (no defaults):**

| Secret | Description |
|---|---|
| `GMAIL_PASSWORD` | Gmail app password |
| `AWS_ACCESS_KEY_ID` | AWS IAM credentials for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM credentials for Bedrock |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_PROJECT_ID` | Google Cloud project ID |
| `BROWSERBASE_API_KEY` | Browserbase API key |
| `BROWSERBASE_PROJECT_ID` | Browserbase project ID |
| `PG_PASSWORD` | Postgres password |

**Optional (have defaults):**

| Secret | Default | Description |
|---|---|---|
| `GMAIL_ACCOUNT` | `sourcing.agent@example.com` | Agent's Gmail address |
| `BEDROCK_REGION` | `ap-southeast-2` | AWS Bedrock region |
| `GOOGLE_REFRESH_TOKEN` | | Stored in Postgres after first OAuth flow |
| `GOOGLE_SHEET_ID` | *(set in config)* | Input/output spreadsheet ID |
| `MAINTAINER_EMAIL_ADDRESS` | | Captcha escalation email |
| `MAX_WORKERS` | `3` | Concurrent pipeline threads |
| `GOOGLE_TOTP_SECRET` | | Google TOTP secret for re-auth |
| `SOURCING_INTERVAL_MINUTES` | `15` | Sourcing pipeline cron interval |
| `NEGOTIATION_INTERVAL_MINUTES` | `30` | Negotiation pipeline cron interval |
| `STALLED_OUTREACH_MINUTES` | `60` | Recovery threshold for stalled threads |

### 3. Tag a release

```bash
./scripts/create_release.sh patch   # v0.0.0 -> v0.0.1
./scripts/create_release.sh minor   # v0.0.1 -> v0.1.0
./scripts/create_release.sh major   # v0.1.0 -> v1.0.0
```

### 4. Deploy

Go to **Actions > Deploy > Run workflow**, select your environment, and run.

On first run the workflow will install Docker and Docker Compose on the server. Subsequent deploys skip this step.

### Server requirements

- Ubuntu 22.04+ LTS
- SSH access on port 22
- No other prerequisites -- Docker and Docker Compose are installed automatically on first deploy
