from council.evidence.base import EvidenceItem, EvidenceTool
from council.evidence.code import CodeExecutionTool
from council.evidence.web import WebSearchTool


def build_tools(settings) -> dict[str, EvidenceTool]:
    """The complete V1 evidence toolset: web retrieval and code execution."""
    return {
        "web": WebSearchTool(
            provider=settings.evidence_search_provider,
            api_key=(
                settings.tavily_api_key
                if settings.evidence_search_provider == "tavily"
                else settings.brave_api_key
            ),
        ),
        "code": CodeExecutionTool(
            enabled=settings.evidence_code_execution,
            timeout_seconds=settings.evidence_code_timeout_seconds,
        ),
    }


__all__ = ["CodeExecutionTool", "EvidenceItem", "EvidenceTool", "WebSearchTool", "build_tools"]
