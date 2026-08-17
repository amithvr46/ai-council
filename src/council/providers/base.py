"""Provider layer.

Frozen architecture rule: a provider exposes generate() and nothing else.
Roles (candidate, check, synthesis, judge, verifier) are prompts plus
orchestration in the engine — never provider methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

# USD per 1M tokens: (input, output). Verified 2026-08-17 against vendor
# pricing pages. Unknown models cost 0 — update this table as pricing changes.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICING.get(model, (0.0, 0.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


@dataclass
class ModelResponse:
    content: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    parsed: Any = None  # populated when a schema was requested
    retried: bool = False  # True when the malformed-output retry was used
    raw: dict = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return cost_for(self.model, self.input_tokens, self.output_tokens)


class ProviderError(Exception):
    """Base for provider failures the engine can degrade on."""

    kind = "api_error"


class ProviderTimeout(ProviderError):
    kind = "timeout"


class ProviderRateLimited(ProviderError):
    kind = "rate_limited"


class MalformedOutput(ProviderError):
    """Schema-validated output still malformed after the single retry."""

    kind = "malformed_output"


class ModelProvider(ABC):
    """The whole provider contract: generate()."""

    name: str

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        """Run one completion. If schema is given, response.parsed holds a
        validated instance; implementations retry exactly once on malformed
        output, then raise MalformedOutput. temperature=None omits the
        parameter — current-generation models reject it as deprecated."""


def validate_or_none(schema: type[BaseModel], text: str) -> BaseModel | None:
    """Best-effort parse of model output against a schema."""
    import json

    try:
        return schema.model_validate(json.loads(_strip_fences(text)))
    except (json.JSONDecodeError, ValidationError):
        return None


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        if t.startswith("json"):
            t = t[4:]
    return t.strip()
