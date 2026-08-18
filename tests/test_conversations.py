"""M3.5: conversations, pinning and follow-up context."""

import asyncio

from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")


def _engine(make_engine, openai_answers=1):
    responses = []
    for i in range(openai_answers):
        responses += [f"GPT answer {i + 1}", AGREE, Synthesis(final_answer=f"Final {i + 1}.")]
    openai = FakeProvider("openai", responses)
    anthropic = FakeProvider(
        "anthropic", [f"Claude answer {i + 1}" for i in range(openai_answers)]
    )
    return make_engine(openai, anthropic, check_provider="openai"), openai, anthropic


async def _ask_and_wait(client, question, conversation_id=None):
    payload = {"question": question, "mode": "council"}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    r = client.post("/ask/async", json=payload)
    assert r.status_code == 200
    body = r.json()
    for _ in range(200):
        trace = client.get(f"/requests/{body['id']}").json()
        if trace["status"] in ("complete", "failed"):
            return body, trace
        await asyncio.sleep(0.02)
    raise AssertionError("request never finished")


async def test_ask_auto_creates_conversation_with_title(make_engine, monkeypatch):
    engine, _, _ = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        body, trace = await _ask_and_wait(client, "How do I rotate AWS keys safely?")
        assert body["conversation_id"]
        conv = client.get(f"/conversations/{body['conversation_id']}").json()
        assert conv["title"].startswith("How do I rotate AWS keys")
        assert len(conv["requests"]) == 1
        assert conv["requests"][0]["final_answer"] == "Final 1."


async def test_follow_up_receives_history(make_engine, monkeypatch):
    engine, openai, anthropic = _engine(make_engine, openai_answers=2)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        body1, _ = await _ask_and_wait(client, "What is Terraform state?")
        await _ask_and_wait(client, "And how do I lock it?", body1["conversation_id"])

    # Second candidate calls must contain the first turn as chat history.
    for fake in (openai, anthropic):
        candidate_calls = [c for c in fake.calls if c["schema"] is None]
        second = candidate_calls[1]
        roles = [m["role"] for m in second["messages"]]
        contents = " ".join(m["content"] for m in second["messages"])
        assert roles.count("user") == 2  # history turn + current question
        assert "What is Terraform state?" in contents
        assert "Final 1." in contents  # prior final answer, not raw candidates


async def test_first_ask_has_no_history(make_engine, monkeypatch):
    engine, openai, _ = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        await _ask_and_wait(client, "Fresh question?")
    first_candidate = next(c for c in openai.calls if c["schema"] is None)
    assert [m["role"] for m in first_candidate["messages"]] == ["system", "user"]


async def test_pinned_conversations_sort_first(make_engine, monkeypatch):
    engine, _, _ = _engine(make_engine, openai_answers=2)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        body1, _ = await _ask_and_wait(client, "older chat")
        body2, _ = await _ask_and_wait(client, "newer chat")
        client.patch(f"/conversations/{body1['conversation_id']}", json={"pinned": True})
        items = client.get("/conversations").json()["items"]
        assert items[0]["id"] == body1["conversation_id"]
        assert items[0]["pinned"] is True
        assert items[1]["id"] == body2["conversation_id"]


async def test_ask_into_unknown_conversation_404(make_engine, monkeypatch):
    engine, _, _ = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.post(
            "/ask/async", json={"question": "q?", "mode": "council", "conversation_id": "nope"}
        )
        assert r.status_code == 404


async def test_rename_conversation(make_engine, monkeypatch):
    engine, _, _ = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        body, _ = await _ask_and_wait(client, "some question")
        client.patch(f"/conversations/{body['conversation_id']}", json={"title": "AWS keys"})
        conv = client.get(f"/conversations/{body['conversation_id']}").json()
        assert conv["title"] == "AWS keys"
