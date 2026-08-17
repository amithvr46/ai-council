from pathlib import Path

from council.engine.prompts import PromptRegistry, default_registry


def test_registry_loads_all_v1_prompts():
    reg = default_registry()
    for name in ["candidate", "combined_check", "synthesis"]:
        p = reg.get(name)
        assert p.version == 1
        assert p.version_id == f"{name}.v1"
        assert len(p.text) > 50


def test_registry_picks_highest_version(tmp_path: Path):
    (tmp_path / "thing.v1.md").write_text("old")
    (tmp_path / "thing.v2.md").write_text("new")
    reg = PromptRegistry(tmp_path)
    assert reg.get("thing").text == "new"
    assert reg.get("thing").version_id == "thing.v2"
