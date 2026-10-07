import os

import httpx
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


class OpenAICompatLLM:
    """Any OpenAI-compatible /chat/completions endpoint: Ollama, Groq, OpenRouter, Gemini, etc.

    Free-model presets (set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY):
      Ollama      http://localhost:11434/v1                                  (no key, local)
      Groq        https://api.groq.com/openai/v1                             (free tier key)
      OpenRouter  https://openrouter.ai/api/v1   model ending in ":free"
      Gemini      https://generativelanguage.googleapis.com/v1beta/openai    (free tier key)
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", client: httpx.Client | None = None):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.price_in = float(os.getenv("LLM_PRICE_IN_PER_MTOK", "0"))
        self.price_out = float(os.getenv("LLM_PRICE_OUT_PER_MTOK", "0"))
        self.client = client or httpx.Client(timeout=float(os.getenv("LLM_TIMEOUT_S", "120")))

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> LLMResult:
        with llm_span("rank-hypotheses", self.model, user) as record:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            r = self.client.post(f"{self.base_url}/chat/completions", headers=headers, json={
                "model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            i, o = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            record(text, {"input": i, "output": o})
        cost = (i * self.price_in + o * self.price_out) / 1_000_000
        LLM_TOKENS.labels("input").inc(i)
        LLM_TOKENS.labels("output").inc(o)
        LLM_COST.inc(cost)
        return LLMResult(text, i, o, cost)


def make_llm():
    """LLM_BASE_URL set -> OpenAI-compatible (free models); else ANTHROPIC_API_KEY -> Claude; else None."""
    base = os.getenv("LLM_BASE_URL")
    if base:
        return OpenAICompatLLM(base, os.getenv("LLM_MODEL") or "llama3.1", os.getenv("LLM_API_KEY", ""))
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    return None
