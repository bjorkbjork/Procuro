import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    GMAIL_ACCOUNT: str = "sourcing.agent@example.com"
    GMAIL_PASSWORD: str

    # AWS Bedrock
    BEDROCK_REGION: str = "ap-southeast-2"
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str

    # Logfire
    LOGFIRE_TOKEN: str = ""


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
    GOOGLE_AUTH_URI: str
    GOOGLE_TOKEN_URI: str
    GOOGLE_AUTH_PROVIDER_X509_CERT_URL: str
    GOOGLE_REFRESH_TOKEN: str

    # the ID for the sheet containing both input/output tabs
    GOOGLE_SHEET_ID: str = "1-3f1S0ditHC70fawiS6tcw2yK7GCWacecKCGXNTl3s4"


settings = Settings()
pg_settings = PostgresSettings()
google_settings = GoogleSettings()
