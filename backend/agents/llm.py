import os
from dataclasses import dataclass

from backend.observability import LLM_COST, LLM_TOKENS, llm_span


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class AnthropicLLM:
    """Thin Claude wrapper. Prices are env-configurable estimates (USD per million tokens)."""

    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.price_in = float(os.getenv("LLM_PRICE_IN_PER_MTOK", "3"))
        self.price_out = float(os.getenv("LLM_PRICE_OUT_PER_MTOK", "15"))
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> LLMResult:
        with llm_span("rank-hypotheses", self.model, user) as record:
            msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                              messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            i, o = msg.usage.input_tokens, msg.usage.output_tokens
            record(text, {"input": i, "output": o})
        cost = (i * self.price_in + o * self.price_out) / 1_000_000
        LLM_TOKENS.labels("input").inc(i)
        LLM_TOKENS.labels("output").inc(o)
        LLM_COST.inc(cost)
        return LLMResult(text, i, o, cost)
