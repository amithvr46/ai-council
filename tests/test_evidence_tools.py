"""The two V1 evidence tools: web retrieval and sandboxed code execution."""

import httpx
import pytest

from council.evidence import build_tools
from council.evidence.code import CodeExecutionTool
from council.evidence.web import WebSearchTool

# --- code execution ----------------------------------------------------------


async def test_code_execution_captures_stdout_and_exit_code():
    tool = CodeExecutionTool(timeout_seconds=15)
    items = await tool.run("print('the answer is', 6 * 7)")
    assert len(items) == 1
    item = items[0]
    assert item.status == "ok"
    assert "the answer is 42" in item.snippet
    assert "exit_code: 0" in item.snippet
    assert item.raw["exit_code"] == 0


async def test_failing_code_is_evidence_not_tool_error():
    """A traceback is exactly the evidence that the code does not work — it
    must reach the assessor, not be swallowed as a tool failure."""
    tool = CodeExecutionTool(timeout_seconds=15)
    items = await tool.run("raise ValueError('boom')")
    item = items[0]
    assert item.status == "ok"  # the tool worked; the code didn't
    assert item.raw["exit_code"] != 0
    assert "ValueError: boom" in item.snippet


async def test_code_execution_times_out_and_is_killed():
    """A runaway loop must always be stopped. The wall-clock timeout is the
    primary mechanism; on POSIX the CPU rlimit is a backstop above it, so
    either path is a pass as long as the loop did not survive."""
    tool = CodeExecutionTool(timeout_seconds=1)
    items = await tool.run("while True:\n    pass")
    item = items[0]
    killed_by_timeout = item.status == "error" and "exceeded 1s" in (item.error or "")
    killed_by_rlimit = item.status == "ok" and item.raw.get("exit_code") not in (0, None)
    assert killed_by_timeout or killed_by_rlimit, item


async def test_code_execution_can_be_disabled():
    tool = CodeExecutionTool(enabled=False)
    items = await tool.run("print(1)")
    assert items[0].status == "unavailable"
    assert "disabled" in items[0].error


async def test_code_runs_in_isolated_temp_directory():
    tool = CodeExecutionTool(timeout_seconds=15)
    items = await tool.run("import os; print('FILES:', sorted(os.listdir('.')))")
    # Only the snippet itself — no access to the repo working directory.
    assert "FILES: ['snippet.py']" in items[0].snippet


async def test_code_output_is_truncated():
    tool = CodeExecutionTool(timeout_seconds=20)
    items = await tool.run("print('x' * 50000)")
    assert len(items[0].snippet) < 20000


# --- web retrieval -----------------------------------------------------------


async def test_web_tool_unavailable_without_key():
    tool = WebSearchTool(provider="tavily", api_key="")
    assert tool.available is False
    items = await tool.run("anything")
    assert items[0].status == "unavailable"
    assert "no web search API key" in items[0].error


async def test_web_tool_parses_tavily_results(monkeypatch):
    payload = {
        "results": [
            {
                "url": "https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/",
                "title": "Encrypting Confidential Data at Rest",
                "content": "Encryption at rest is not enabled by default.",
                "score": 0.98,
            }
        ]
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    tool = WebSearchTool(provider="tavily", api_key="k")
    items = await tool.run("k8s secret encryption default")
    assert items[0].status == "ok"
    assert items[0].source_url.startswith("https://kubernetes.io")
    assert "not enabled by default" in items[0].snippet
    assert "[E1] (web)" in items[0].as_context(1)


async def test_web_tool_http_error_becomes_evidence_gap(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    tool = WebSearchTool(provider="tavily", api_key="k")
    items = await tool.run("q")
    assert items[0].status == "error"
    assert "ConnectError" in items[0].error


def test_build_tools_returns_exactly_the_v1_toolset():
    class S:
        evidence_search_provider = "none"
        tavily_api_key = ""
        brave_api_key = ""
        evidence_code_execution = True
        evidence_code_timeout_seconds = 15

    tools = build_tools(S())
    assert set(tools) == {"web", "code"}
    assert tools["web"].available is False  # no key -> honest unavailability
    assert tools["code"].available is True


@pytest.mark.parametrize("status", ["error", "unavailable"])
def test_failed_items_render_visibly_in_context(status):
    from council.evidence.base import EvidenceItem

    item = EvidenceItem(kind="web", query="q", status=status, error="nope")
    assert "UNAVAILABLE" in item.as_context(3)
    assert "[E3]" in item.as_context(3)
