"""M3: async ask, SSE stream, history list, stats."""

import asyncio
import json

from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.engine.events import EventBus
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
SYNTH = Synthesis(final_answer="Final.")


def _engine(make_engine, publish=None):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    e = make_engine(openai, anthropic, check_provider="openai")
    if publish is not None:
        e._publish = publish
    return e


async def test_async_ask_returns_id_and_completes(make_engine, monkeypatch):
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.post("/ask/async", json={"question": "q?", "mode": "council"})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert r.json()["status"] == "running"

        for _ in range(100):
            trace = client.get(f"/requests/{rid}").json()
            if trace["status"] in ("complete", "failed"):
                break
            await asyncio.sleep(0.02)
        assert trace["status"] == "complete"
        assert trace["final_answer"] == "Final."


async def test_engine_emits_stage_and_done_events(make_engine):
    events = []
    engine = _engine(make_engine, publish=lambda rid, e: events.append(e))

    await engine.run("q?", "council")

    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert types[-1] == "done"
    stages = [e["stage"] for e in events if e["type"] == "stage"]
    assert {"candidate_a", "candidate_b", "combined_check", "synthesis"} <= set(stages)
    done = events[-1]
    assert done["status"] == "complete" and done["degraded"] is False


async def test_stream_endpoint_replays_done_for_finished_request(make_engine, monkeypatch):
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    result = await engine.run("q?", "council")
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        with client.stream("GET", f"/requests/{result['id']}/stream") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["status"] == "complete"


async def test_stream_404_for_unknown_request(make_engine, monkeypatch):
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        assert client.get("/requests/nope/stream").status_code == 404


async def test_list_requests_paginates_newest_first(make_engine, monkeypatch):
    openai = FakeProvider("openai", ["a1", AGREE, SYNTH, "a2", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["b1", "b2"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)
    await engine.run("first?", "council")
    await engine.run("second?", "council")

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.get("/requests", params={"limit": 1}).json()
        assert r["total"] == 2
        assert len(r["items"]) == 1
        assert r["items"][0]["question"] == "second?"
        r2 = client.get("/requests", params={"limit": 1, "offset": 1}).json()
        assert r2["items"][0]["question"] == "first?"


async def test_stats_aggregates(make_engine, monkeypatch):
    engine = _engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    await engine.run("q?", "council")

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        s = client.get("/stats").json()
    assert s["today"]["requests"] == 1
    assert s["month"]["requests"] == 1
    assert s["by_mode"]["council"]["requests"] == 1
    assert s["degraded_rate"] == 0.0


def test_event_bus_pub_sub_unsubscribe():
    bus = EventBus()
    q = bus.subscribe("r1")
    bus.publish("r1", {"type": "stage"})
    bus.publish("r2", {"type": "stage"})  # different request: not delivered
    assert q.qsize() == 1
    bus.unsubscribe("r1", q)
    bus.publish("r1", {"type": "done"})
    assert q.qsize() == 1  # nothing new after unsubscribe
