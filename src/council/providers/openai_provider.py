import time

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from council.providers.base import (
    MalformedOutput,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    validate_or_none,
)


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(self, api_key: str, timeout: float = 120.0):
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

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
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        # Current-generation models reject temperature as deprecated;
        # only send it when a caller explicitly asks.
        if temperature is not None:
            kwargs["temperature"] = temperature
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            }

        started = time.monotonic()
        try:
            if on_delta is not None:
                content, in_tok, out_tok = await self._stream(kwargs, on_delta)
            else:
                resp = await self._client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                in_tok = resp.usage.prompt_tokens if resp.usage else 0
                out_tok = resp.usage.completion_tokens if resp.usage else 0
        except RateLimitError as e:
            raise ProviderRateLimited(str(e)) from e
        except APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except APIError as e:
            raise ProviderError(str(e)) from e
        latency_ms = int((time.monotonic() - started) * 1000)

        result = ModelResponse(
            content=content,
            provider=self.name,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
        )

        if schema is not None:
            parsed = validate_or_none(schema, content)
            if parsed is None:
                # One retry, with the failure fed back.
                retry_messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON for the required "
                            "schema. Respond again with ONLY a valid JSON object."
                        ),
                    },
                ]
                kwargs["messages"] = retry_messages
                started = time.monotonic()
                try:
                    resp2 = await self._client.chat.completions.create(**kwargs)
                except RateLimitError as e:
                    raise ProviderRateLimited(str(e)) from e
                except APITimeoutError as e:
                    raise ProviderTimeout(str(e)) from e
                except APIError as e:
                    raise ProviderError(str(e)) from e
                content2 = resp2.choices[0].message.content or ""
                parsed = validate_or_none(schema, content2)
                result.content = content2
                result.input_tokens += resp2.usage.prompt_tokens if resp2.usage else 0
                result.output_tokens += resp2.usage.completion_tokens if resp2.usage else 0
                result.latency_ms += int((time.monotonic() - started) * 1000)
                result.retried = True
                result.api_attempts = 2
                if parsed is None:
                    raise MalformedOutput(
                        f"{model} returned malformed output twice for {schema.__name__}"
                    )
            result.parsed = parsed

        return result

    async def _stream(self, kwargs: dict, on_delta) -> tuple[str, int, int]:
        stream = await self._client.chat.completions.create(
            **kwargs, stream=True, stream_options={"include_usage": True}
        )
        content, in_tok, out_tok = "", 0, 0
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                content += delta
                on_delta(delta)
            if chunk.usage:
                in_tok = chunk.usage.prompt_tokens or 0
                out_tok = chunk.usage.completion_tokens or 0
        return content, in_tok, out_tok
