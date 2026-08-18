"""Wire settings + providers + prompts into a ready CouncilEngine."""

from council.config import get_settings
from council.engine.events import bus
from council.engine.pipeline import CouncilEngine
from council.engine.prompts import default_registry
from council.providers.anthropic_provider import AnthropicProvider
from council.providers.openai_provider import OpenAIProvider


def build_engine() -> CouncilEngine:
    s = get_settings()
    # Empty keys get a placeholder so the app can boot without credentials;
    # the first live call then fails loudly with an auth error, which the
    # pipeline records and degrades on — better than refusing to start.
    providers = {
        "openai": OpenAIProvider(
            s.openai_api_key or "unset", timeout=s.request_timeout_seconds
        ),
        "anthropic": AnthropicProvider(
            s.anthropic_api_key or "unset", timeout=s.request_timeout_seconds
        ),
    }
    return CouncilEngine(
        providers,
        default_registry(),
        flagship_models={
            "openai": s.openai_model_flagship,
            "anthropic": s.anthropic_model_flagship,
        },
        cheap_models={
            "openai": s.openai_model_cheap,
            "anthropic": s.anthropic_model_cheap,
        },
        check_provider=s.check_provider,
        judge_provider=s.judge_provider,
        quick_mode_strategy=s.quick_mode_strategy,
        publish=bus.publish,
    )
