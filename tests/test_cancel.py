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
