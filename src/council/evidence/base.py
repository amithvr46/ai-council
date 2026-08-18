"""Evidence tools.

Frozen V1 rule: evidence outranks model opinion. Tools return facts the
pipeline can check claims against — they never reason. Exactly two tools
exist in V1: web retrieval and sandboxed code execution.

Every tool result is persisted (see db.models.EvidenceItem) so a decision
can be audited afterwards: what was searched or run, what came back, and
which claim it bore on.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EvidenceItem:
    """One unit of retrieved evidence."""

    kind: str  # "web" | "code"
    query: str  # search query, or the executed source
    status: str = "ok"  # ok | error | unavailable
    source_url: str | None = None
    title: str | None = None
    snippet: str = ""  # the actual content the assessor reads
    error: str | None = None
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)

    def as_context(self, index: int) -> str:
        """Rendered form given to the assessor — numbered so claims can cite it."""
        if self.status != "ok":
            return f"[E{index}] ({self.kind}) UNAVAILABLE: {self.error or self.status}"
        head = f"[E{index}] ({self.kind})"
        if self.source_url:
            head += f" {self.title or ''} — {self.source_url}"
        return f"{head}\n{self.snippet}".strip()


class EvidenceTool(ABC):
    name: str
    available: bool = True

    @abstractmethod
    async def run(self, query: str) -> list[EvidenceItem]:
        """Gather evidence for one query. Never raises for expected failures —
        returns items with status 'error'/'unavailable' so the pipeline can
        record the gap and preserve uncertainty instead of guessing."""
