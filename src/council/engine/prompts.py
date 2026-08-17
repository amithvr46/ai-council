"""Versioned prompt registry.

Prompts live in prompts/<name>.v<N>.md. The registry returns the highest
version for a name and stamps `<name>.v<N>` into every step row so the
learning layer can attribute outcomes to exact prompt versions.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_PATTERN = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def version_id(self) -> str:
        return f"{self.name}.v{self.version}"


class PromptRegistry:
    def __init__(self, prompts_dir: Path | str):
        self._dir = Path(prompts_dir)
        self._cache: dict[str, Prompt] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self._dir.glob("*.md")):
            m = _PATTERN.match(path.name)
            if not m:
                continue
            name, version = m.group("name"), int(m.group("version"))
            existing = self._cache.get(name)
            if existing is None or version > existing.version:
                self._cache[name] = Prompt(name, version, path.read_text())

    def get(self, name: str) -> Prompt:
        if name not in self._cache:
            raise KeyError(f"No prompt named {name!r} in {self._dir}")
        return self._cache[name]


def default_registry() -> PromptRegistry:
    # repo_root/prompts — resolved relative to this file so the CLI works
    # from any working directory.
    return PromptRegistry(Path(__file__).resolve().parents[3] / "prompts")
