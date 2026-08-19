"""CLI ingestion path.

The CLI is how career sources actually get in — the user runs it on their own
machine against real documents that never enter the repo. If it silently
stores an unreadable file, the whole grounding chain is compromised at the
first step, so these cover the refusal paths as well as the happy one.
"""

from typer.testing import CliRunner

from council.cli import app

runner = CliRunner()


def _invoke(tmp_path, monkeypatch, *args):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    import council.config
    import council.db.session as session

    council.config.get_settings.cache_clear()
    session._engine = None
    session._sessionmaker = None
    return runner.invoke(app, list(args))


def test_ingest_then_profile_roundtrip(tmp_path, monkeypatch):
    resume = tmp_path / "master.txt"
    resume.write_text("Built Harness pipelines, Terraform Enterprise workspaces and AKS clusters.")

    r = _invoke(tmp_path, monkeypatch, "ingest", str(resume), "--authority", "master_resume")
    assert r.exit_code == 0, r.output
    assert "ingested:" in r.output

    r2 = _invoke(tmp_path, monkeypatch, "profile")
    assert r2.exit_code == 0, r2.output
    assert "harness" in r2.output
    assert "terraform enterprise" in r2.output


def test_ingest_refuses_unreadable_file(tmp_path, monkeypatch):
    bad = tmp_path / "scan.tiff"
    bad.write_bytes(b"II*\x00nonsense")
    r = _invoke(tmp_path, monkeypatch, "ingest", str(bad), "--authority", "master_resume")
    assert r.exit_code == 1
    assert "cannot read" in r.output

    # Nothing was stored. An empty document here reads as an empty career.
    r2 = _invoke(tmp_path, monkeypatch, "profile", "--sources")
    assert "master_resume" not in r2.output


def test_ingest_rejects_unknown_authority(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("text")
    r = _invoke(tmp_path, monkeypatch, "ingest", str(f), "--authority", "gospel")
    assert r.exit_code == 2
    assert "gospel" in r.output


def test_missing_file_is_reported_not_crashed(tmp_path, monkeypatch):
    r = _invoke(tmp_path, monkeypatch, "ingest", str(tmp_path / "nope.txt"))
    assert r.exit_code == 2
    assert "no such file" in r.output


def test_analyze_jd_names_what_the_career_cannot_support(tmp_path, monkeypatch):
    jd = tmp_path / "jd.txt"
    jd.write_text(
        "Infrastructure Engineer. Primarily Google Cloud Platform: GKE, Cloud Run, "
        "Cloud SQL. Terraform for all infrastructure. Kubernetes in production."
    )
    r = _invoke(tmp_path, monkeypatch, "analyze-jd", str(jd))
    assert r.exit_code == 0, r.output
    assert "gcp" in r.output
    assert "gke" in r.output
    # The supported line must still credit real experience.
    assert "terraform" in r.output


def test_profile_set_is_persisted(tmp_path, monkeypatch):
    r = _invoke(tmp_path, monkeypatch, "profile-set", "employers", "Example Corp")
    assert r.exit_code == 0, r.output
    r2 = _invoke(tmp_path, monkeypatch, "profile")
    assert "Example Corp" in r2.output


def test_profile_set_rejects_unknown_field(tmp_path, monkeypatch):
    r = _invoke(tmp_path, monkeypatch, "profile-set", "salary", "999")
    assert r.exit_code == 2
    assert "unknown field" in r.output
