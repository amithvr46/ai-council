"use client";

import { StageEvent } from "@/lib/api";

const LABELS: Record<string, string> = {
  candidate_a: "Candidate A",
  candidate_b: "Candidate B",
  candidate_fallback: "Failover",
  combined_check: "Agreement check",
  synthesis: "Synthesis",
  critique_of_a: "Critique of A",
  critique_of_b: "Critique of B",
  judge: "Judge",
  verifier: "Verifier",
  revision: "Revision",
  disagreement_report: "Disagreement report",
  candidate: "Answer",
};

export default function PipelineView({
  events,
  running,
}: {
  events: StageEvent[];
  running: boolean;
}) {
  const stages = events.filter((e) => e.type === "stage");
  return (
    <div className="flex flex-wrap items-center gap-2">
      {stages.map((e, i) => (
        <span
          key={i}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs ${
            e.status === "error"
              ? "border-red-800 bg-red-950 text-red-300"
              : "border-emerald-900 bg-emerald-950 text-emerald-300"
          }`}
          title={e.model ?? undefined}
        >
          {e.status === "error" ? "✕" : "✓"} {LABELS[e.stage ?? ""] ?? e.stage}
          {e.provider && <span className="text-zinc-500">· {e.provider}</span>}
          {typeof e.latency_ms === "number" && (
            <span className="text-zinc-500">{(e.latency_ms / 1000).toFixed(1)}s</span>
          )}
        </span>
      ))}
      {running && (
        <span className="inline-flex items-center gap-2 rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300">
          <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
          council is thinking…
        </span>
      )}
    </div>
  );
}
