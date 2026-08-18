import json
import time

from anthropic import APIError, APITimeoutError, AsyncAnthropic, RateLimitError
from pydantic import BaseModel, ValidationError

from council.providers.base import (
    MalformedOutput,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, api_key: str, timeout: float = 120.0):
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        on_delta=None,
    ) -> ModelResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        chat = [m for m in messages if m["role"] != "system"]

        kwargs: dict = {
            "model": model,
            "messages": chat,
            "max_tokens": max_tokens,
        }
        # Current-generation models reject temperature as deprecated;
        # only send it when a caller explicitly asks.
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        if schema is not None:
            # Structured output via forced tool use — the reliable way to get
            # schema-conforming JSON out of the Anthropic API.
            kwargs["tools"] = [
                {
                    "name": "emit",
                    "description": f"Emit the {schema.__name__} result.",
                    "input_schema": schema.model_json_schema(),
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "emit"}

        if on_delta is not None:
            text, tool_json, in_tok, out_tok, latency_ms = await self._call_streaming(
                kwargs, on_delta
            )
            result = ModelResponse(
                content=text or tool_json,
                provider=self.name,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
            )
            parsed = None
            if schema is not None:
                try:
                    parsed = _validate_payload(json.loads(tool_json or "null"), schema)
                except json.JSONDecodeError:
                    parsed = None
        else:
            response, latency_ms = await self._call(kwargs)
            result = self._to_result(response, model, latency_ms)
            parsed = self._parse_tool_input(response, schema) if schema is not None else None

        if schema is not None:
            if parsed is None:
                # One retry, never streamed.
                response2, latency2 = await self._call(kwargs)
                parsed = self._parse_tool_input(response2, schema)
                retry = self._to_result(response2, model, latency2)
                result.input_tokens += retry.input_tokens
                result.output_tokens += retry.output_tokens
                result.latency_ms += retry.latency_ms
                result.content = retry.content or result.content
                result.retried = True
                result.api_attempts = 2
                if parsed is None:
                    raise MalformedOutput(
                        f"{model} returned malformed output twice for {schema.__name__}"
                    )
            result.parsed = parsed

        return result

    async def _call(self, kwargs: dict):
        started = time.monotonic()
        try:
            response = await self._client.messages.create(**kwargs)
        except RateLimitError as e:
            raise ProviderRateLimited(str(e)) from e
        except APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except APIError as e:
            raise ProviderError(str(e)) from e
        return response, int((time.monotonic() - started) * 1000)

    async def _call_streaming(self, kwargs: dict, on_delta):
        """Returns (text, tool_json, input_tokens, output_tokens, latency_ms).
        on_delta receives text deltas for plain calls and raw partial JSON
        for tool (schema) calls."""
        started = time.monotonic()
        text_parts: list[str] = []
        json_parts: list[str] = []
        in_tok = out_tok = 0
        try:
            stream = await self._client.messages.create(**kwargs, stream=True)
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "message_start":
                    in_tok = event.message.usage.input_tokens
                elif etype == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", "") == "text_delta":
                        text_parts.append(delta.text)
                        on_delta(delta.text)
                    elif getattr(delta, "type", "") == "input_json_delta":
                        json_parts.append(delta.partial_json)
                        on_delta(delta.partial_json)
                elif etype == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None and usage.output_tokens is not None:
                        out_tok = usage.output_tokens
        except RateLimitError as e:
            raise ProviderRateLimited(str(e)) from e
        except APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except APIError as e:
            raise ProviderError(str(e)) from e
        latency_ms = int((time.monotonic() - started) * 1000)
        return "".join(text_parts), "".join(json_parts), in_tok, out_tok, latency_ms

    @staticmethod
    def _to_result(response, model: str, latency_ms: int) -> ModelResponse:
        text = "".join(b.text for b in response.content if b.type == "text")
        return ModelResponse(
            content=text,
            provider="anthropic",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_tool_input(response, schema: type[BaseModel]):
        for block in response.content:
            if block.type == "tool_use" and block.name == "emit":
                return _validate_payload(block.input, schema)
        return None


def _validate_payload(data, schema: type[BaseModel]):
    """Validate, unwrapping the intermittent single-key envelope some models
    emit ({'parameters': {...}} / {'input': {...}})."""
    candidates = [data]
    if isinstance(data, dict) and len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            candidates.append(inner)
    for candidate in candidates:
        try:
            return schema.model_validate(candidate)
        except ValidationError:
            continue
    return None
