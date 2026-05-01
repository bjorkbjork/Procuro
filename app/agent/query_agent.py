"""Generates supplier search queries from product specs. Uses a cheap model
since this is straightforward extraction — pull key attributes and form a search string.
"""

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.base.config import model_settings
from app.base.llm import get_model

SYSTEM_PROMPT = """\
You generate search queries for supplier sourcing platforms based on product specifications.

Given a product title and its specs, return ONLY a concise search query — what you'd
type into a supplier platform search bar.

Focus on attributes that matter for sourcing: size, resolution, technology, power.
Drop branding, marketing terms, and retailer-specific naming.
Keep the query under 10 words. Return nothing but the query string."""


query_agent = Agent(
    model=get_model(model_settings.CHEAP),
    system_prompt=SYSTEM_PROMPT,
    output_type=str,
    retries=2,
    model_settings=ModelSettings(temperature=0.9),
)


def _build_prompt(title: str, specs: dict) -> str:
    prompt = f"Product: {title}\n\nSpecifications:\n"
    for group, items in specs.items():
        prompt += f"\n{group}:\n"
        for k, v in items.items():
            prompt += f"  {k}: {v}\n"
    return prompt


def generate_search_queries(title: str, specs: dict, count: int = 5) -> list[str]:
    prompt = _build_prompt(title, specs)
    queries = set()
    attempts = 0
    max_attempts = count * 2

    while len(queries) < count and attempts < max_attempts:
        result = query_agent.run_sync(prompt)
        queries.add(result.output.strip())
        attempts += 1

    return list(queries)
