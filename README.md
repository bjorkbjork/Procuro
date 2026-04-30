# Supplier Sourcing Agent

Autonomous agent that sources supplier quotes for Kogan products from GlobalSources and Alibaba, negotiates FOB prices via email, and logs results to Google Sheets.

See `SPEC.md` for the full technical specification.

## Prerequisites

### Python 3.12

Install via your package manager or [pyenv](https://github.com/pyenv/pyenv). This project uses [PDM](https://pdm-project.org/) for dependency management.

### PostgreSQL

A local Postgres instance with a database and user created:

```sql
CREATE USER agent WITH PASSWORD 'your_password';
CREATE DATABASE "sourcingAgentDb" OWNER agent;
```

### AWS Bedrock

An AWS account with Bedrock model access enabled for Claude Sonnet in your chosen region. You'll need an IAM user with `bedrock:InvokeModel` permissions.

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

A [Browserbase](https://www.browserbase.com/) account for cloud browser sessions. Residential proxies are used for AU geolocation to bypass Kogan's bot detection.

- `BROWSERBASE_API_KEY`
- `BROWSERBASE_PROJECT_ID`

## Setup

```bash
pdm install
alembic upgrade head
```

Copy `.env.example` to `.env` and fill in all required values (see `app/base/config.py` for the full list).

## Running

```bash
pdm run python -m app.agent.main
```

## Testing

```bash
pdm run pytest
```
