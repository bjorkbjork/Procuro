# New attrs must also be added to .github/workflows/deploy.yml.
# Pydantic defaults apply unless overridden via a GitHub Secret of the same name.
import logging
from pathlib import Path

import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    AGENT_NAME: str
    AGENT_EMAIL: str
    AGENT_COMPANY_DESCRIPTION: str = (
        "over high-volume recurring orders.5
    AUTOMATION_FAILURE_ALERT_WINDOW_MINUTES: int = 30
    AUTOMATION_FAILURE_ALERT_MIN_EVENTS: int = 5
    REAUTH_MAX_RETRIES: int = 5
    LIBREOFFICE_TIMEOUT_SECONDS: int = 60


class PostgresSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}
    # Database
    PG_USER: str = "sourcing_agent"
    PG_PASSWORD: str = ""
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    DB_NAME: str = "sourcingAgentDb"


class GoogleSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_PROJECT_ID: str
    GOOGLE_AUTH_URI: str = "https://accounts.google.com/o/oauth2/auth"
    GOOGLE_TOKEN_URI: str = "https://oauth2.googleapis.com/token"
    GOOGLE_AUTH_PROVIDER_X509_CERT_URL: str = (
        "https://www.googleapis.com/oauth2/v1/certs"
    )
    GOOGLE_REFRESH_TOKEN: str = ""

    GOOGLE_SHEET_ID: str


class BrowserbaseSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    BROWSERBASE_API_KEY: str
    BROWSERBASE_PROJECT_ID: str


class CaptchaSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    TWOCAPTCHA_API_KEY: str = ""


class ModelSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    CHEAP: str = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    MODERATE: str = "au.anthropic.claude-sonnet-4-6"
    # Если у тебя есть много денег :) (И, да, я говорю по-русски немного. Это не ИИ)
    EXPENSIVE: str = "au.anthropic.claude-opus-4-6-v1"

    # Model pools for rate limit rotation: list of (model_id, rpm_limit)
    CHEAP_POOL: list[tuple[str, int]] = [
        ("qwen.qwen3-32b-v1:0", 100),
        ("mistral.ministral-3-8b-instruct", 100),
        ("au.anthropic.claude-haiku-4-5-20251001-v1:0", 10),
    ]
    MODERATE_POOL: list[tuple[str, int]] = [
        ("mistral.mistral-large-3-675b-instruct", 100),
        ("deepseek.v3.2", 100),
        ("moonshotai.kimi-k2.5", 100),
        ("au.anthropic.claude-sonnet-4-6", 10),
    ]
    EXPENSIVE_POOL: list[tuple[str, int]] = [
        ("mistral.mistral-large-3-675b-instruct", 100),
        ("deepseek.v3.2", 100),
        ("au.anthropic.claude-opus-4-6-v1", 10),
    ]
    MATCH_POOL: list[tuple[str, int]] = [
        ("moonshotai.kimi-k2.5", 100),
        ("deepseek.v3.2", 100),
        ("mistral.mistral-large-3-675b-instruct", 100),
    ]
    # Negotiation agent receives PDF attachments — only models supporting documents
    NEGOTIATION_POOL: list[tuple[str, int]] = [
        ("au.anthropic.claude-sonnet-4-6", 10),
    ]
    # Browser agents send screenshots — excludes models with low image limits
    BROWSER_POOL: list[tuple[str, int]] = [
        ("amazon.nova-pro-v1:0", 100),
        ("qwen.qwen3-vl-235b-a22b", 100),
        ("moonshotai.kimi-k2.5", 100),
        ("au.anthropic.claude-sonnet-4-6", 10),
    ]


class SchedulerSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    SOURCING_INTERVAL_MINUTES: int = 15
    NEGOTIATION_INTERVAL_MINUTES: int = 30
    STALLED_OUTREACH_MINUTES: int = 60
    STALLED_NEGOTIATION_MINUTES: int = 120
    MAX_NEGOTIATION_FAILURES: int = 3
    MAX_SEARCH_ATTEMPTS: int = 5


settings = Settings()
pg_settings = PostgresSettings()
google_settings = GoogleSettings()
browserbase_settings = BrowserbaseSettings()
captcha_settings = CaptchaSettings()
model_settings = ModelSettings()
scheduler_settings = SchedulerSettings()


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(threadName)s] %(name)s %(levelname)s %(message)s",
    )
