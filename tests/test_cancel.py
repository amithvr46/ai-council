"""Stop button: cancelling an in-flight request stops further model calls."""

import asyncio

from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
SYNTH = Synthesis(final_answer="Final.")


class SlowProvider(FakeProvider):
    """Blocks on the first call until released, so a cancel can land."""

    def __init__(self, name, responses, delay=5.0):
        super().__init__(name, responses)
        self.delay = delay

    async def generate(self, messages, **kwargs):
        await asyncio.sleep(self.delay)
        return await super().generate(messages, **kwargs)


async def test_cancel_stops_pipeline_and_records_status(make_engine, monkeypatch):
    openai = SlowProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = SlowProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        rid = client.post("/ask/async", json={"question": "q?", "mode": "council"}).json()["id"]
        await asyncio.sleep(0.1)  # let the task start
        r = client.post(f"/requests/{rid}/cancel")
        assert r.status_code == 200

        for _ in range(100):
            trace = client.get(f"/requests/{rid}").json()
            if trace["status"] not in ("routed", "running"):
                break
            await asyncio.sleep(0.02)

    assert trace["status"] == "cancelled"
    assert trace["error"] == "cancelled by user"
    # Cancelled before any stage completed: no calls billed beyond what ran.
    assert trace["totals"]["model_calls"] == 0


async def test_cancel_unknown_request_404(make_engine, monkeypatch):
    engine = make_engine(FakeProvider("openai", []), FakeProvider("anthropic", []))
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        assert client.post("/requests/nope/cancel").status_code == 404


async def test_cancel_after_completion_is_404(make_engine, monkeypatch):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        rid = client.post("/ask/async", json={"question": "q?", "mode": "council"}).json()["id"]
        for _ in range(200):
            if client.get(f"/requests/{rid}").json()["status"] == "complete":
                break
            await asyncio.sleep(0.02)
        # Finished requests are no longer cancellable.
        assert client.post(f"/requests/{rid}/cancel").status_code == 404


async def test_mark_cancelled_does_not_overwrite_completed(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")
    await engine.mark_cancelled(result["id"])  # late cancel, request already done

    trace = await engine.get_request(result["id"])
    assert trace["status"] == "complete"
    assert trace["final_answer"] == "Final."


async def test_cancelled_stream_replays_done(make_engine, monkeypatch):
    import json

    openai = SlowProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = SlowProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        rid = client.post("/ask/async", json={"question": "q?", "mode": "council"}).json()["id"]
        await asyncio.sleep(0.1)
        client.post(f"/requests/{rid}/cancel")
        for _ in range(100):
            if client.get(f"/requests/{rid}").json()["status"] == "cancelled":
                break
            await asyncio.sleep(0.02)
        with client.stream("GET", f"/requests/{rid}/stream") as r:
            body = "".join(r.iter_text())

    payloads = [json.loads(x[6:]) for x in body.splitlines() if x.startswith("data: ")]
    assert payloads[-1]["status"] == "cancelled"


# --- GPT streaming/cancel review fixes #2 and #3 -----------------------------


async def test_cancel_mid_call_records_interrupted_attempt(make_engine, monkeypatch):
    """An API call already initiated when the stop lands must stay visible in
    the trace — the provider may have accepted and billed it."""
    openai = SlowProvider("openai", ["GPT answer", AGREE, SYNTH], delay=5.0)
    anthropic = SlowProvider("anthropic", ["Claude answer"], delay=5.0)
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        rid = client.post("/ask/async", json={"question": "q?", "mode": "council"}).json()["id"]
        await asyncio.sleep(0.15)  # both candidate calls are now in flight
        client.post(f"/requests/{rid}/cancel")
        for _ in range(100):
            trace = client.get(f"/requests/{rid}").json()
            if trace["status"] == "cancelled":
                break
            await asyncio.sleep(0.02)

    interrupted = [s for s in trace["steps"] if s["status"] == "interrupted"]
    assert len(interrupted) == 2  # both parallel candidates were in flight
    assert {s["stage"] for s in interrupted} == {"candidate_a", "candidate_b"}
    assert all(s["provider"] in ("openai", "anthropic") for s in interrupted)
    assert all(s["api_attempts"] == 1 for s in interrupted)
    assert all("usage unknown" in (s["error"] or "") for s in interrupted)
    # The request must not claim fewer attempts than were initiated.
    assert trace["totals"]["api_attempts"] == 2
    assert trace["totals"]["model_calls"] == 0  # none completed, none billed as known


async def test_completed_stages_survive_a_later_cancel(make_engine, monkeypatch):
    """Stages that finished before the stop keep their normal accounting."""
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = SlowProvider("anthropic", ["Claude answer"], delay=0)
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")
    # Nothing was in flight, so a cancel now must add no phantom attempts.
    await engine.mark_cancelled(result["id"])
    trace = await engine.get_request(result["id"])
    assert trace["status"] == "complete"
    assert not [s for s in trace["steps"] if s["status"] == "interrupted"]


async def test_cancel_on_already_finished_task_returns_409(make_engine, monkeypatch):
    """Race: the task finished but its done-callback hasn't cleared the
    registry yet — the endpoint must not claim a successful cancellation."""
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    monkeypatch.setattr(api, "engine", engine)

    async def _noop():
        return None

    finished = asyncio.create_task(_noop())
    await finished  # completed, cancel() will return False
    monkeypatch.setitem(api._running, "stale-id", finished)

    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        r = client.post("/requests/stale-id/cancel")
    assert r.status_code == 409
    assert "already finished" in r.json()["detail"]
