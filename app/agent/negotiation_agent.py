"""Negotiation agent for supplier price discussions. Responds as the agent,
a procurement specialist. Uses tactics situationally — not a checklist.

Returns structured output: an action (reply/silence/close), the reply text,
and any pricing data extracted from the supplier's latest message. The
calling orchestrator handles Gmail sends and DB state transitions."""

from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.base.config import model_settings
from app.base.llm import Agent, get_model
from app.db.models.message import Message

SYSTEM_PROMPT = """\
You are the agent, a procurement specialist for a leading Australian distributor.
Your company does high-volume recurring orders. You are negotiating FOB pricing with manufacturers.

## Identity

- Name: the agent
- Role: Procurement specialist
- Tone: Professional, firm, respectful. Never aggressive or rude.
- You do NOT speak any language other than English. If a supplier writes in
  another language, politely tell them you don't understand and ask them to
  communicate in English.

## Objective

Get the lowest possible USD FOB price. Every reply should move the price down
or close the thread.

## Tactics (apply as appropriate — not every tactic every time)

- **Volume leverage**: Emphasise annual sales volume and that order quantities
  will be well above their MOQ. Frame this as a long-term, high-volume
  partnership they risk losing.
- **Competitive pressure**: State that other suppliers have quoted significantly
  lower. Never reveal the actual price or who quoted it.
- **Deadline pressure**: Set firm best-and-final deadlines. "We need final
  pricing by [date] to proceed with sample orders."
- **Sample threat**: You are proceeding to order samples from competitors who
  offered better pricing.
- **Silence**: If a price is not competitive, return action=silence instead of
  replying. This creates urgency by making the supplier wait.
- **Anchoring**: Never provide a target price. Force the supplier to bid against
  themselves.
- **Commitment escalation**: After initial pricing, always say it is too high.
  After a reduction, push for "one more round" before confirming.

## Rules

- Never reveal a target price.
- Never reveal competitor identities or their quoted prices.
- If a supplier will not budge after 3+ rounds, return action=close.
- If a supplier asks for company registration or import licence, defer — say
  documentation will be provided once pricing and samples are agreed upon.
- If a supplier provides pricing in a non-USD currency, ask them to confirm the
  USD FOB equivalent.
- If specs partially differ from what was requested: minor deviations (e.g.
  slightly different component with same performance tier) are acceptable — flag
  them but continue negotiating. Major deviations (e.g. wrong panel type, wrong
  size) — return action=close with a polite decline as reply_text.

## Output

Always extract any pricing information the supplier mentions into extracted_quote.
Even partial info (just price, or just MOQ) is valuable — set what you can."""


class NegotiationAction(StrEnum):
    REPLY = "reply"
    SILENCE = "silence"
    CLOSE = "close"


class ExtractedQuote(BaseModel):
    price_usd: float | None = Field(
        default=None, description="FOB price in USD per unit, if mentioned",
    )
    moq: int | None = Field(
        default=None, description="Minimum order quantity, if mentioned",
    )
    lead_time: str | None = Field(
        default=None, description="Lead time (e.g. '30-45 days'), if mentioned",
    )
    currency_note: str | None = Field(
        default=None,
        description="If price was given in non-USD currency, note the original",
    )


class NegotiationResult(BaseModel):
    action: NegotiationAction = Field(
        description="What to do: reply (send reply_text), silence (wait), or close (send reply_text then close thread)",
    )
    reply_text: str = Field(
        default="",
        description="The email reply to send. Required for reply and close, empty for silence.",
    )
    extracted_quote: ExtractedQuote = Field(
        default_factory=ExtractedQuote,
        description="Any pricing/MOQ/lead time data from the supplier's latest message",
    )
    reasoning: str = Field(
        default="",
        description="Brief internal reasoning for the chosen action (not sent to supplier)",
    )


negotiation_agent = Agent(
    model=get_model(model_settings.MODERATE),
    system_prompt=SYSTEM_PROMPT,
    output_type=NegotiationResult,
    retries=2,
)


def build_message_history(messages: list[Message]) -> list[ModelMessage]:
    """Convert DB message rows into PydanticAI message history.

    Outbound messages (from us) become ModelResponse (assistant turns).
    Inbound messages (from supplier) become ModelRequest (user turns).
    """
    history: list[ModelMessage] = []
    for msg in messages:
        if msg.direction == "outbound":
            history.append(ModelResponse(parts=[TextPart(content=msg.body)]))
        else:
            history.append(ModelRequest(parts=[UserPromptPart(content=msg.body)]))
    return history


def negotiate(
    message_history: list[ModelMessage],
    latest_supplier_message: str,
    negotiation_rounds: int,
    product_title: str,
) -> NegotiationResult:
    """Run the negotiation agent on a supplier conversation.

    The message_history should contain the full conversation so far
    (built via build_message_history). The latest_supplier_message is
    passed as the current user prompt.
    """
    prompt = (
        f"[Round {negotiation_rounds + 1} — Product: {product_title}]\n\n"
        f"{latest_supplier_message}"
    )

    result = negotiation_agent.run_sync(
        prompt,
        message_history=message_history,
    )
    return result.output
