"""Deterministic fake providers for pipeline tests."""

import json

from pydantic import BaseModel

from council.providers.base import ModelProvider, ModelResponse


class Retried:
    """Wrap a queued payload to simulate a generation whose malformed-output
    retry fired: the logical call succeeds but cost 2 physical API attempts."""

    def __init__(self, payload):
        self.payload = payload


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
        temperature: float | None = None,
        max_tokens: int = 4096,
        on_delta=None,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "model": model, "schema": schema})
        if not self.responses:
            raise AssertionError(f"FakeProvider {self.name} ran out of queued responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        retried = False
        if isinstance(item, Retried):
            item, retried = item.payload, True
        if on_delta is not None:
            # Simulate streaming: raw JSON for schema calls, plain text
            # otherwise, delivered in two chunks.
            raw = json.dumps(item.model_dump()) if isinstance(item, BaseModel) else str(item)
            mid = max(1, len(raw) // 2)
            on_delta(raw[:mid])
            on_delta(raw[mid:])
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
                retried=retried,
                api_attempts=2 if retried else 1,
            )
        return ModelResponse(
            content=str(item),
            provider=self.name,
            model=model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=5,
            retried=retried,
            api_attempts=2 if retried else 1,
        )
