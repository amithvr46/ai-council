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
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        # Some reasoning-class models reject temperature; only send non-default.
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
            resp = await self._client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            raise ProviderRateLimited(str(e)) from e
        except APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except APIError as e:
            raise ProviderError(str(e)) from e
        latency_ms = int((time.monotonic() - started) * 1000)

        content = resp.choices[0].message.content or ""
        result = ModelResponse(
            content=content,
            provider=self.name,
            model=model,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
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
                if parsed is None:
                    raise MalformedOutput(
                        f"{model} returned malformed output twice for {schema.__name__}"
                    )
            result.parsed = parsed

        return result
