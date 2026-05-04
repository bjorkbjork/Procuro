"""Compares a source product's specs against a supplier listing to determine
whether they describe the same (or functionally equivalent) product. Uses a
moderate model for semantic understanding of spec equivalences."""

from pydantic import BaseModel, Field

from app.base.config import model_settings
from app.base.llm import Agent, get_model

SYSTEM_PROMPT = """\
You are a product specification comparison expert for wholesale sourcing.

Given a reference product (from a retailer) and a candidate product (from a supplier),
determine whether the candidate is worth sending an inquiry to — i.e. could plausibly
be the same product or a functionally equivalent alternative suitable for resale.

Your job is to CAST A WIDE NET. We will confirm exact specs during outreach. Only
reject candidates that are clearly wrong products (e.g. completely different category,
wildly different size, fundamentally different technology like OLED vs LED).

Scoring guidance:
- 0.7-1.0: Strong match — core specs align, minor or no differences
- 0.5-0.7: Plausible match — right category and ballpark specs, some gaps or ambiguity
- 0.3-0.5: Worth investigating — same product category, key specs unclear or partially matching
- 0.0-0.3: Clear mismatch — wrong product type, wrong size class, incompatible technology

IMPORTANT:
- Ambiguous or missing specs are NOT reasons to reject. Supplier listings are often
  sparse or use generic descriptions. Score these as "worth investigating" (0.4-0.6).
- Configurable products (e.g. "43-85 inch") that INCLUDE the reference size should be
  scored as plausible matches, not penalised for offering a range.
- Weight, brightness, or minor feature differences do not disqualify — these vary by
  SKU configuration and are confirmed during outreach.
- When in doubt, set is_match=true. A false positive costs one email; a false negative
  loses a potential supplier."""


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
    model=get_model("match", pool=model_settings.MATCH_POOL),
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
