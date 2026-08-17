from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider


def _fake_engine(make_engine):
    openai = FakeProvider(
        "openai",
        [
            "GPT answer",
            CombinedCheck(agreement="agree", disagreement_type="none", summary="same"),
            Synthesis(final_answer="Final."),
        ],
    )
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    return make_engine(openai, anthropic, check_provider="openai")


async def test_ask_and_fetch_roundtrip(make_engine, monkeypatch):
    engine = _fake_engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)

    with TestClient(app, raise_server_exceptions=True) as client:
        # lifespan replaced our engine; put the fake back
        monkeypatch.setattr(api, "engine", engine)
        r = client.post("/ask", json={"question": "q?", "mode": "council"})
        assert r.status_code == 200
        body = r.json()
        assert body["final_answer"] == "Final."

        r2 = client.get(f"/requests/{body['id']}")
        assert r2.status_code == 200
        assert len(r2.json()["steps"]) >= 4

        r3 = client.post(f"/requests/{body['id']}/rating", json={"rating": 5})
        assert r3.status_code == 200
        assert client.get(f"/requests/{body['id']}").json()["user_rating"] == 5


async def test_bad_mode_rejected(make_engine, monkeypatch):
    engine = _fake_engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        r = client.post("/ask", json={"question": "q?", "mode": "forever"})
        assert r.status_code == 422


async def test_unknown_request_404(make_engine, monkeypatch):
    engine = _fake_engine(make_engine)
    monkeypatch.setattr(api, "engine", engine)
    with TestClient(app) as client:
        monkeypatch.setattr(api, "engine", engine)
        assert client.get("/requests/nope").status_code == 404
