"""Sandboxed code execution evidence.

Running a snippet is the only way to settle "does this code work?" without
asking a model its opinion — which is the entire point of the evidence
layer for coding questions.

Isolation in V1: a separate Python process, a fresh temporary working
directory, a scrubbed environment, a hard timeout, output truncation and
(on POSIX) address-space/CPU/file-size rlimits. This is a personal tool
running model-written code on the user's own machine — it is a blast-radius
limiter, NOT a security boundary against deliberately hostile code. It can
be switched off entirely with EVIDENCE_CODE_EXECUTION=false.
"""

import asyncio
import os
import sys
import tempfile
import time

from council.evidence.base import EvidenceItem, EvidenceTool

MAX_OUTPUT_CHARS = 4000


def _posix_limits(memory_mb: int, cpu_seconds: int):  # pragma: no cover - POSIX only
    import resource

    def apply() -> None:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

    return apply


class CodeExecutionTool(EvidenceTool):
    name = "code"

    def __init__(
        self,
        enabled: bool = True,
        timeout_seconds: int = 15,
        memory_mb: int = 512,
    ):
        self.available = enabled
        self._timeout = timeout_seconds
        self._memory_mb = memory_mb

    async def run(self, query: str) -> list[EvidenceItem]:
        """`query` is the Python source to execute."""
        if not self.available:
            return [
                EvidenceItem(
                    kind="code",
                    query=query,
                    status="unavailable",
                    error="code execution disabled (EVIDENCE_CODE_EXECUTION=false)",
                )
            ]

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="council-exec-") as workdir:
            script = os.path.join(workdir, "snippet.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(query)

            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": workdir,
                "TMPDIR": workdir,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                # Deny outbound network at the library level for the common
                # clients; not a hard block, documented as such.
                "no_proxy": "*",
                "NO_PROXY": "*",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            }
            kwargs: dict = {}
            if os.name == "posix":
                # CPU rlimit sits ABOVE the wall-clock timeout so the timeout
                # is the primary, deterministic stop; the rlimit is the
                # backstop for a process that ignores it.
                kwargs["preexec_fn"] = _posix_limits(self._memory_mb, self._timeout + 5)

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",  # isolated mode: ignore env python paths and user site
                    script,
                    cwd=workdir,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **kwargs,
                )
            except OSError as e:
                return [
                    EvidenceItem(
                        kind="code",
                        query=query,
                        status="error",
                        error=f"could not start interpreter: {e}",
                    )
                ]

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
                timed_out = False
            except TimeoutError:
                proc.kill()
                await proc.wait()
                stdout, stderr, timed_out = b"", b"", True

        latency = int((time.monotonic() - started) * 1000)
        if timed_out:
            return [
                EvidenceItem(
                    kind="code",
                    query=query,
                    status="error",
                    error=f"execution exceeded {self._timeout}s and was killed",
                    latency_ms=latency,
                )
            ]

        out = stdout.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS]
        err = stderr.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS]
        exit_code = proc.returncode
        snippet = (
            f"exit_code: {exit_code}\n"
            f"stdout:\n{out or '(empty)'}\n"
            f"stderr:\n{err or '(empty)'}"
        )
        return [
            EvidenceItem(
                kind="code",
                query=query,
                # A non-zero exit is a VALID result, not a tool failure: it is
                # exactly the evidence that the code does not work.
                status="ok",
                snippet=snippet,
                latency_ms=latency,
                raw={"exit_code": exit_code, "timed_out": False},
            )
        ]
