from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""

    database_url: str = "postgresql+asyncpg://council:council@localhost:5432/council"

    openai_model_flagship: str = "gpt-5.6-sol"
    openai_model_cheap: str = "gpt-5.6-luna"
    anthropic_model_flagship: str = "claude-sonnet-5"
    anthropic_model_cheap: str = "claude-haiku-4-5"

    check_provider: Literal["openai", "anthropic"] = "openai"
    judge_provider: Literal["openai", "anthropic"] = "anthropic"
    quick_mode_strategy: Literal["alternate", "openai", "anthropic"] = "alternate"
    request_timeout_seconds: int = 120

    # --- evidence layer (deep mode) ---
    evidence_search_provider: Literal["tavily", "brave", "none"] = "none"
    tavily_api_key: str = ""
    brave_api_key: str = ""
    evidence_code_execution: bool = True
    evidence_code_timeout_seconds: int = 15
    max_web_searches: int = 3
    max_code_executions: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
