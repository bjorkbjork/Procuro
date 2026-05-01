"""Shared Bedrock provider and model factories for all agents."""

import boto3
from botocore.config import Config
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

from app.base.config import model_settings, settings

_provider = None


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
