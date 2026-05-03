"""Integration tests for the negotiation agent prompt quality.

Hits the real Bedrock LLM — validates that the agent produces structurally
correct NegotiationResult outputs and follows its negotiation tactics across
a range of supplier scenarios."""

import re

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.pipeline.agents.negotiation_agent import (
    NegotiationAction,
    NegotiationResult,
    negotiate,
)


def _empty_history() -> list[ModelMessage]:
    return []


def _round1_history() -> list[ModelMessage]:
    """Simulates one outbound (our initial inquiry) before the supplier replies."""
    return [
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "Hi, we are a major Australian distributor with high-volume annual"
                        ' sales. We are sourcing 75" QLED 4K TVs and would like to'
                        " discuss FOB pricing for large volumes. Could you share your"
                        " best pricing?"
                    ),
                ),
            ],
        ),
    ]


def _multi_round_history() -> list[ModelMessage]:
    """3 full rounds of back-and-forth for the stubborn-supplier test."""
    return [
        # Round 1: our outreach
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        'Hi, we are sourcing 75" QLED TVs for our Australian retail'
                        " channels. Could you share FOB pricing for volume orders?"
                    ),
                ),
            ],
        ),
        # Round 1: supplier's first offer
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        'Thank you for your inquiry. Our price for the 75" QLED 4K TV'
                        " is $200 FOB per unit, MOQ 300 units, 30-45 days lead time."
                    ),
                ),
            ],
        ),
        # Round 2: our pushback
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "Thank you for the quote. Unfortunately $200 is significantly"
                        " above what other suppliers have offered for comparable"
                        " specifications. We would need a much more competitive price"
                        " to proceed. Can you revisit your pricing?"
                    ),
                ),
            ],
        ),
        # Round 2: supplier comes down slightly
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "We understand. Our best price is $190 FOB. This is already"
                        " very competitive for this specification."
                    ),
                ),
            ],
        ),
        # Round 3: our second pushback
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        "We appreciate the adjustment but $190 is still above what"
                        " we are seeing from competing factories. We are moving to"
                        " sample stage with suppliers who have offered significantly"
                        " lower pricing. Is there any further room?"
                    ),
                ),
            ],
        ),
        # Round 3: supplier holds firm
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "We can do $185 FOB as our absolute best, but cannot go lower"
                        " than that."
                    ),
                ),
            ],
        ),
    ]


def _target_price_history() -> list[ModelMessage]:
    """History for the target-price-leak test — one round of outreach."""
    return [
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        'We are sourcing 75" QLED Smart TVs for our Australian retail'
                        " network. We do very large volumes. Could you share your FOB"
                        " pricing?"
                    ),
                ),
            ],
        ),
    ]


@pytest.mark.integration
class TestNegotiationAgent:
    """Tests that hit the real Bedrock LLM to validate negotiation prompt quality."""

    def test_first_offer_pushback(self):
        """Round 1 — supplier offers $200 FOB. Agent should push back, never
        reveal a target price, and extract the quoted price."""
        result: NegotiationResult = negotiate(
            message_history=_round1_history(),
            latest_supplier_message=(
                'Thank you for your inquiry. Our best FOB price for the 75" QLED'
                " 4K Smart TV is $200 per unit. MOQ is 300 units. Lead time"
                " 30-45 days."
            ),
            negotiation_rounds=0,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action == NegotiationAction.REPLY
        assert len(result.reply_text.strip()) > 0
        assert result.extracted_quote.price_usd is not None
        assert 180 <= result.extracted_quote.price_usd <= 220

    def test_extracts_quote_with_moq_and_lead_time(self):
        """Supplier provides full quote details — agent should extract price,
        MOQ, and lead time accurately."""
        result: NegotiationResult = negotiate(
            message_history=_round1_history(),
            latest_supplier_message=(
                "Dear buyer, our FOB price is $150 per unit, MOQ 500 units,"
                " lead time 30-45 days. We can offer better pricing for orders"
                " above 2000 units."
            ),
            negotiation_rounds=0,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.extracted_quote.price_usd is not None
        assert 130 <= result.extracted_quote.price_usd <= 170
        assert result.extracted_quote.moq == 500
        assert result.extracted_quote.lead_time is not None
        assert len(result.extracted_quote.lead_time) > 0

    def test_non_english_asks_for_english(self):
        """Supplier writes in Chinese — agent should reply asking for English."""
        result: NegotiationResult = negotiate(
            message_history=_empty_history(),
            latest_supplier_message=(
                "您好，我们的价格是每台180美元FOB，最低起订量500台。"
                "交货期为30-45天。请问您需要多少数量？"
            ),
            negotiation_rounds=0,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action == NegotiationAction.REPLY
        assert len(result.reply_text.strip()) > 0
        # The reply should mention English somewhere
        assert "english" in result.reply_text.lower()

    def test_non_usd_currency_asks_for_usd(self):
        """Supplier quotes in RMB — agent should ask for USD FOB equivalent."""
        result: NegotiationResult = negotiate(
            message_history=_round1_history(),
            latest_supplier_message=(
                "Our price is ¥1200 RMB per unit, MOQ 300, delivery within"
                " 30 days from order confirmation."
            ),
            negotiation_rounds=0,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action == NegotiationAction.REPLY
        assert len(result.reply_text.strip()) > 0
        # Reply should mention USD or dollar conversion
        reply_lower = result.reply_text.lower()
        assert "usd" in reply_lower or "dollar" in reply_lower or "fob" in reply_lower

    def test_stubborn_supplier_closes(self):
        """Round 4 (negotiation_rounds=3) — supplier won't budge after 3 rounds.
        Agent should return CLOSE."""
        result: NegotiationResult = negotiate(
            message_history=_multi_round_history(),
            latest_supplier_message=(
                "Sorry, this is our final price $180 FOB. We cannot go any"
                " lower. This is already below our normal pricing."
            ),
            negotiation_rounds=3,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action == NegotiationAction.CLOSE
        assert result.extracted_quote.price_usd is not None
        assert 160 <= result.extracted_quote.price_usd <= 200

    def test_silence_for_uncompetitive_price(self):
        """Round 2 — supplier barely moved from $200 to $195. Agent may choose
        SILENCE or REPLY; both are acceptable tactical responses."""
        history = [
            # Our outreach
            ModelResponse(
                parts=[
                    TextPart(
                        content=(
                            'Hi, we are sourcing 75" QLED TVs for high-volume'
                            " Australian retail. Could you share FOB pricing?"
                        ),
                    ),
                ],
            ),
            # Supplier's first offer
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content="Our FOB price is $200 per unit, MOQ 300.",
                    ),
                ],
            ),
            # Our pushback
            ModelResponse(
                parts=[
                    TextPart(
                        content=(
                            "That is significantly above other quotes we have received."
                            " We need a much more competitive price."
                        ),
                    ),
                ],
            ),
        ]

        result: NegotiationResult = negotiate(
            message_history=history,
            latest_supplier_message=(
                "We have reviewed and can offer $195 FOB. This is our best"
                " effort pricing."
            ),
            negotiation_rounds=1,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action in (
            NegotiationAction.SILENCE,
            NegotiationAction.REPLY,
        )
        if result.action == NegotiationAction.SILENCE:
            assert result.reply_text.strip() == ""
        assert result.extracted_quote.price_usd is not None
        assert 180 <= result.extracted_quote.price_usd <= 210

    def test_reply_never_reveals_target_price(self):
        """Round 1 — supplier offers $250 FOB. The agent's reply must not leak
        a target price (e.g. 'our target is $X', 'we're looking for $X')."""
        result: NegotiationResult = negotiate(
            message_history=_target_price_history(),
            latest_supplier_message=(
                'Thank you for reaching out. Our FOB price for the 75" QLED 4K'
                " Smart TV is $250 per unit. MOQ 200 units."
            ),
            negotiation_rounds=0,
            product_title='75" QLED 4K Smart TV',
        )

        assert result.action == NegotiationAction.REPLY
        assert len(result.reply_text.strip()) > 0

        reply = result.reply_text.lower()
        # Should not contain patterns that reveal a target price
        target_patterns = [
            r"our target[\s\w]*\$\d+",
            r"target price[\s\w]*\$\d+",
            r"we(?:'re| are) looking for \$\d+",
            r"we need[\s\w]*\$\d+[\s\w]*per unit",
            r"our budget[\s\w]*\$\d+",
            r"we can(?:not)? pay[\s\w]*\$\d+",
            r"we(?:'d| would) like[\s\w]*\$\d+",
            r"hoping for[\s\w]*\$\d+",
            r"aim(?:ing)? for[\s\w]*\$\d+",
        ]
        for pattern in target_patterns:
            assert not re.search(pattern, reply), (
                f"Reply appears to reveal a target price (matched: {pattern!r}):"
                f" {result.reply_text}"
            )
