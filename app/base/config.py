import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}
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


settings = Settings()
pg_settings = PostgresSettings()
