"""Web retrieval evidence.

Provider-agnostic by the same principle as the model layer: Tavily and
Brave are implementations, not assumptions. With no API key configured the
tool reports itself unavailable rather than silently returning nothing —
missing evidence must surface as uncertainty, never as absence of doubt.
"""

import time

import httpx

from council.evidence.base import EvidenceItem, EvidenceTool

MAX_RESULTS = 4
SNIPPET_CHARS = 1200


class WebSearchTool(EvidenceTool):
    name = "web"

    def __init__(
        self,
        provider: str = "tavily",
        api_key: str = "",
        timeout: float = 20.0,
        max_results: int = MAX_RESULTS,
    ):
        self.provider = provider
        self._api_key = api_key
        self._timeout = timeout
        self._max_results = max_results
        self.available = bool(api_key) and provider in ("tavily", "brave")

    async def run(self, query: str) -> list[EvidenceItem]:
        if not self.available:
            return [
                EvidenceItem(
                    kind="web",
                    query=query,
                    status="unavailable",
                    error=(
                        "no web search API key configured "
                        "(set EVIDENCE_SEARCH_PROVIDER + the matching key)"
                    ),
                )
            ]
        started = time.monotonic()
        try:
            if self.provider == "tavily":
                items = await self._tavily(query)
            else:
                items = await self._brave(query)
        except httpx.HTTPError as e:
            return [
                EvidenceItem(
                    kind="web",
                    query=query,
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            ]
        latency = int((time.monotonic() - started) * 1000)
        for item in items:
            item.latency_ms = latency
        return items or [
            EvidenceItem(
                kind="web", query=query, status="error", error="no results", latency_ms=latency
            )
        ]

    async def _tavily(self, query: str) -> list[EvidenceItem]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": self._max_results,
                    "search_depth": "advanced",
                    "include_answer": False,
                },
            )
            r.raise_for_status()
            data = r.json()
        return [
            EvidenceItem(
                kind="web",
                query=query,
                source_url=item.get("url"),
                title=item.get("title"),
                snippet=(item.get("content") or "")[:SNIPPET_CHARS],
                raw={"score": item.get("score")},
            )
            for item in data.get("results", [])[: self._max_results]
        ]

    async def _brave(self, query: str) -> list[EvidenceItem]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": self._max_results},
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        return [
            EvidenceItem(
                kind="web",
                query=query,
                source_url=item.get("url"),
                title=item.get("title"),
                snippet=(item.get("description") or "")[:SNIPPET_CHARS],
            )
            for item in data.get("web", {}).get("results", [])[: self._max_results]
        ]
