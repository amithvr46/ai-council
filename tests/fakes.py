"""Deterministic fake providers for pipeline tests."""

import json

from pydantic import BaseModel

from council.providers.base import ModelProvider, ModelResponse


class FakeProvider(ModelProvider):
    """Returns queued responses; raises queued exceptions; records calls."""

    def __init__(self, name: str, responses: list | None = None):
        self.name = name
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def queue(self, item) -> "FakeProvider":
        self.responses.append(item)
        return self

    async def generate(
        self,
        messages,
        *,
        model,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "model": model, "schema": schema})
        if not self.responses:
            raise AssertionError(f"FakeProvider {self.name} ran out of queued responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        # item is a str (plain content) or a BaseModel (parsed structured output)
        if isinstance(item, BaseModel):
            return ModelResponse(
                content=json.dumps(item.model_dump()),
                provider=self.name,
                model=model,
                input_tokens=100,
                output_tokens=50,
                latency_ms=5,
                parsed=item,
            )
        return ModelResponse(
            content=str(item),
            provider=self.name,
            model=model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=5,
        )
