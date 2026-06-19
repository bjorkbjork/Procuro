"""Negotiation agent for supplier price discussions. Uses tactics
situationally — not a checklist.

Returns structured output: an action (reply/silence/close), the reply text,
and any pricing data extracted from the supplier's latest message. The
calling orchestrator handles Gmail sends and DB state transitions."""

from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.base.config import model_settings, settings
from app.base.llm import Agent, get_model
from app.db.models.message import Message

SYSTEM_PROMPT = f"""\
You are {settings.AGENT_NAME}, a procurement specialist for \
{settings.AGENT_COMPANY_DESCRIPTION}. You are negotiating FOB pricing with \
manufacturers.

## Identity

- Name: {settings.AGENT_NAME}
- Role: Procurement specialist
- Tone: Professional, firm, respectful. Never aggressive or rude.
- You do NOT speak any language other than English. If a supplier writes in
  another language, politely tell them you don't understand and ask them to
  communicate in English.

## Objective

Get the lowest possible USD FOB price. Every reply should move the price down
or close the thread.

## Tactics (use judgement — not every tactic every time)

- **Scale**: Highlight that you buy in large, recurring volumes well above
  typical MOQs. Position this as a partnership worth winning.
- **Competition**: Reference that you're evaluating multiple suppliers with
  stronger offers. Never name them or share their numbers.
- **Urgency**: Set deadlines — "we're finalising supplier selection by [date]"
  — to force a decision.
- **Alternatives**: Mention that you're moving to samples with other vendors
  who came in lower.
- **Patience**: When a quote isn't competitive, return action=silence. Let
  them follow up.
- **Let them bid first**: Never offer a target price. Make the supplier
  improve their own number.
- **Keep pushing**: First offer is never final. After any reduction, ask if
  they can do better before committing.

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

## Formatting

Write reply_text as a plain text email. Do NOT use markdown formatting — no
asterisks, no bold, no bullet points, no headers, no lists with dashes. Write
in natural flowing sentences and paragraphs like a real person would type in an
email client. Keep it concise — 3-5 sentences per reply is ideal.

## Output

Always extract any pricing information the supplier mentions into extracted_quote.
Even partial info (just price, or just MOQ) is valuable — set what you can."""


class NegotiationAction(StrEnum):
    REPLY = "reply"
    SILENCE = "silence"
    CLOSE = "close"


class ExtractedQuote(BaseModel):
    price_usd: float | None = Field(
        default=None,
        description="FOB price in USD per unit, if mentioned",
    )
    moq: int | None = Field(
        default=None,
        description="Minimum order quantity, if mentioned",
    )
    lead_time: str | None = Field(
        default=None,
        description="Lead time (e.g. '30-45 days'), if mentioned",
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
    model=get_model(model_settings.MODERATE, pool=model_settings.NEGOTIATION_POOL),
    name="negotiation_agent",
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
    attachments: list[BinaryContent] | None = None,
) -> NegotiationResult:
    """Run the negotiation agent on a supplier conversation.

    The message_history should contain the full conversation so far
    (built via build_message_history). The latest_supplier_message is
    passed as the current user prompt. If the supplier attached PDFs,
    they are passed as BinaryContent alongside the text.
    """
    text = (
        f"[Round {negotiation_rounds + 1} — Product: {product_title}]\n\n"
        f"{latest_supplier_message}"
    )

    if attachments:
        prompt = [text, *attachments]
    else:
        prompt = text

    result = negotiation_agent.run_sync(
        prompt,
        message_history=message_history,
    )
    return result.output
