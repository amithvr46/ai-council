"""Ingestion endpoints (Phase 2B).

These tests exist to hold the contract's structural rules in place at the API
boundary, not just in the pure functions:

  - a JD is the target, never career evidence
  - a tailored resume that omits a technology never removes it
  - unparseable input is refused, never stored as an empty resume
"""

import io

from docx import Document as DocxDocument
from fastapi.testclient import TestClient

import council.api.main as api
from council.api.main import app
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider


def _client(make_engine, monkeypatch) -> TestClient:
    """The lifespan builds a real engine from env; swap in a fake so these
    tests never touch a provider."""
    engine = make_engine(
        FakeProvider(
            "openai",
            [
                "a",
                CombinedCheck(agreement="agree", disagreement_type="none", summary="s"),
                Synthesis(final_answer="f"),
            ],
        ),
        FakeProvider("anthropic", ["b"]),
        check_provider="openai",
    )
    client = TestClient(app, raise_server_exceptions=True)
    client.__enter__()
    monkeypatch.setattr(api, "engine", engine)
    return client


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = DocxDocument()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _upload(client, name: str, data: bytes, authority: str, title: str = ""):
    return client.post(
        "/documents",
        files={"file": (name, data, "application/octet-stream")},
        data={"authority": authority, "title": title},
    )


async def test_upload_extracts_and_lists(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        r = _upload(
            client,
            "master.docx",
            _docx_bytes(["Built Terraform modules and Harness pipelines."]),
            "master_resume",
            "Master resume",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["detected_kind"] == "docx"
        assert body["authority"] == "master_resume"
        assert body["char_count"] > 0
        assert body["duplicate"] is False

        listed = client.get("/documents").json()["items"]
        assert [d["id"] for d in listed] == [body["id"]]

        full = client.get(f"/documents/{body['id']}").json()
        assert "Harness" in full["text"]
    finally:
        client.__exit__(None, None, None)


async def test_identical_content_is_not_stored_twice(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        data = b"Terraform, Azure DevOps, AKS"
        first = _upload(client, "a.txt", data, "supporting").json()
        second = _upload(client, "a-copy.txt", data, "supporting").json()
        assert second["duplicate"] is True
        assert second["id"] == first["id"]
        assert len(client.get("/documents").json()["items"]) == 1
    finally:
        client.__exit__(None, None, None)


async def test_unparseable_upload_is_refused_not_stored_empty(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        r = _upload(client, "scan.tiff", b"II*\x00garbage", "master_resume")
        assert r.status_code == 422
        assert "tiff" in r.json()["detail"].lower()
        # The critical half: nothing was stored. An empty document here would
        # look like a resume with no experience.
        assert client.get("/documents").json()["items"] == []
    finally:
        client.__exit__(None, None, None)


async def test_empty_file_is_refused(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        r = _upload(client, "empty.txt", b"", "supporting")
        assert r.status_code == 422
        assert client.get("/documents").json()["items"] == []
    finally:
        client.__exit__(None, None, None)


async def test_unknown_authority_rejected(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        r = _upload(client, "a.txt", b"text", "gospel")
        assert r.status_code == 422
        assert "gospel" in r.json()["detail"]
    finally:
        client.__exit__(None, None, None)


async def test_jd_is_never_career_evidence(db, make_engine, monkeypatch):
    """The rule that stops the system claiming whatever the JD asks for."""
    client = _client(make_engine, monkeypatch)
    try:
        before = set(client.get("/career-profile").json()["confirmed"])
        assert "gcp" not in before

        _upload(
            client,
            "caveonix.txt",
            b"Requires deep GCP experience, Google Kubernetes Engine and Cloud Run.",
            "jd",
            "Caveonix Infrastructure Engineer",
        )
        after = client.get("/career-profile").json()
        assert "gcp" not in after["confirmed"]
        # ...but the same text as a career source WOULD confirm it, which is
        # what makes the exclusion meaningful rather than accidental.
        assert set(after["confirmed"]) == before
    finally:
        client.__exit__(None, None, None)


async def test_tailored_resume_omission_is_not_negative_evidence(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        _upload(
            client,
            "master.txt",
            b"Harness pipelines, Terraform Enterprise workspaces, Splunk dashboards.",
            "master_resume",
        )
        confirmed = client.get("/career-profile").json()
        assert "harness" in confirmed["confirmed"]

        # A tailored resume written for an AWS role mentions none of it.
        _upload(client, "tailored.txt", b"AWS, EKS, CloudWatch.", "tailored_resume")
        after = client.get("/career-profile").json()
        assert "harness" in after["confirmed"]
        assert "splunk" in after["confirmed"]
        assert "aws" in after["confirmed"]
        assert any(
            s.startswith("master_resume:") for s in after["sources"]["harness"]
        )
    finally:
        client.__exit__(None, None, None)


async def test_profile_put_is_additive_and_survives_reload(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        baseline = client.get("/career-profile").json()["profile"]
        r = client.put(
            "/career-profile",
            json={
                "technologies": [*baseline["technologies"], "Vault"],
                "roles": ["DevOps Engineer"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "vault" in body["confirmed"]
        assert "devops engineer" in body["confirmed"]
        # Untouched fields keep the seeded baseline rather than being wiped.
        assert body["profile"]["domains"] == baseline["domains"]
        assert client.get("/career-profile").json()["confirmed"] == body["confirmed"]
    finally:
        client.__exit__(None, None, None)


async def test_analyze_jd_reports_unsupported_emphasis(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        r = client.post(
            "/career-profile/analyze-jd",
            json={
                "text": (
                    "Site Reliability Engineer. Own incident response, observability "
                    "and reliability for production Kubernetes."
                )
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["role_family"] == "sre"
        assert "incident response" in body["emphasis_supported"]
        assert set(body["emphasis_supported"]) | set(body["emphasis_unsupported"]) == set(
            body["emphasis"]
        )
    finally:
        client.__exit__(None, None, None)


async def test_analyze_jd_reports_technology_gaps(db, make_engine, monkeypatch):
    """The most useful thing a JD analysis can say to a candidate: here is what
    this role wants that you cannot claim."""
    client = _client(make_engine, monkeypatch)
    try:
        body = client.post(
            "/career-profile/analyze-jd",
            json={
                "text": (
                    "Infrastructure Engineer on Google Cloud Platform. Manage GKE "
                    "and Cloud Run, write Terraform, operate Kubernetes in production."
                )
            },
        ).json()
        assert "gcp" in body["technologies_unsupported"]
        assert "gke" in body["technologies_unsupported"]
        assert "terraform" in body["technologies_supported"]
        assert not set(body["technologies_supported"]) & set(body["technologies_unsupported"])
    finally:
        client.__exit__(None, None, None)


async def test_analyze_jd_requires_text(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        assert client.post("/career-profile/analyze-jd", json={"text": "  "}).status_code == 422
    finally:
        client.__exit__(None, None, None)


async def test_patch_and_delete_document(db, make_engine, monkeypatch):
    client = _client(make_engine, monkeypatch)
    try:
        doc = _upload(client, "notes.txt", b"Ansible playbooks.", "supporting").json()
        r = client.patch(
            f"/documents/{doc['id']}", json={"title": "Project notes", "authority": "profile"}
        )
        assert r.json()["title"] == "Project notes"
        assert r.json()["authority"] == "profile"

        assert client.delete(f"/documents/{doc['id']}").status_code == 200
        assert client.get(f"/documents/{doc['id']}").status_code == 404
        assert client.delete(f"/documents/{doc['id']}").status_code == 404
    finally:
        client.__exit__(None, None, None)
