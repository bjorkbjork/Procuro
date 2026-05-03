"""Compares a source product's specs against a supplier listing to determine
whether they describe the same (or functionally equivalent) product. Uses a
moderate model for semantic understanding of spec equivalences."""

from pydantic import BaseModel, Field

from app.base.config import model_settings
from app.base.llm import Agent, get_model

SYSTEM_PROMPT = """\
You are a product specification comparison expert for wholesale sourcing.

Given a reference product (from a retailer) and a candidate product (from a supplier),
determine whether the candidate is a viable match — i.e. could be the same product
or a functionally equivalent alternative suitable for resale.

Consider:
- Core technology must match (e.g. QLED vs OLED is a mismatch)
- Key dimensions/sizes must match or be within acceptable range
- Resolution, power, capacity etc. should be equivalent
- Minor cosmetic or branding differences are acceptable
- Missing specs in the candidate are not automatic disqualifiers — suppliers
  often list fewer details than retailers

Be pragmatic: the goal is sourcing, not exact SKU matching."""


class MatchResult(BaseModel):
    is_match: bool = Field(description="Whether the candidate is a viable match")
    confidence: float = Field(
        description="Confidence score from 0.0 to 1.0", ge=0.0, le=1.0
    )
    reasoning: str = Field(description="Brief explanation of the match decision")
    key_differences: list[str] = Field(
        description="Notable differences between the products",
        default_factory=list,
    )


match_agent = Agent(
    model=get_model(model_settings.MODERATE),
    name="match_agent",
    system_prompt=SYSTEM_PROMPT,
    output_type=MatchResult,
    retries=2,
)


def compare_products(
    reference_title: str,
    reference_specs: dict,
    candidate_title: str,
    candidate_details: dict,
) -> MatchResult:
    prompt = f"REFERENCE PRODUCT:\nTitle: {reference_title}\n\nSpecifications:\n"
    for group, items in reference_specs.items():
        prompt += f"\n{group}:\n"
        for k, v in items.items():
            prompt += f"  {k}: {v}\n"

    prompt += f"\n\nCANDIDATE PRODUCT:\nTitle: {candidate_title}\n"
    if candidate_details:
        prompt += "\nDetails:\n"
        for k, v in candidate_details.items():
            prompt += f"  {k}: {v}\n"

    result = match_agent.run_sync(prompt)
    return result.output
