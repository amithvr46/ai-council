"""Wire settings + providers + prompts into a ready CouncilEngine."""

from council.config import get_settings
from council.engine.events import bus
from council.engine.pipeline import CouncilEngine
from council.engine.prompts import default_registry
from council.evidence import build_tools
from council.providers.anthropic_provider import AnthropicProvider
from council.providers.openai_provider import OpenAIProvider


def build_engine(data_class: str = "real") -> CouncilEngine:
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
        data_class=data_class,
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
        evidence_tools=build_tools(s),
        max_web_searches=s.max_web_searches,
        max_code_executions=s.max_code_executions,
    )


def build_resume_workflow():
    """The Phase 2C artifact workflow.

    The draft and the review deliberately sit on different providers: a model
    reviewing its own writing rates it generously, which is the same reason the
    council pipeline puts the verifier on the opposite provider.
    """
    from council.documents.workflow import ResumeWorkflow

    s = get_settings()
    providers = {
        "openai": OpenAIProvider(s.openai_api_key or "unset", timeout=s.request_timeout_seconds),
        "anthropic": AnthropicProvider(
            s.anthropic_api_key or "unset", timeout=s.request_timeout_seconds
        ),
    }
    draft_provider = s.judge_provider if s.judge_provider in providers else "anthropic"
    review_provider = "openai" if draft_provider == "anthropic" else "anthropic"
    return ResumeWorkflow(
        providers,
        default_registry(),
        draft_provider=draft_provider,
        review_provider=review_provider,
        flagship_models={
            "openai": s.openai_model_flagship,
            "anthropic": s.anthropic_model_flagship,
        },
        cheap_models={
            "openai": s.openai_model_cheap,
            "anthropic": s.anthropic_model_cheap,
        },
    )
