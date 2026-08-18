"use client";

import { use, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Trace, getTrace } from "@/lib/api";
import Rating from "@/components/Rating";
import TraceView from "@/components/TraceView";

export default function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTrace(id).then(setTrace).catch((e) => setError(String(e)));
  }, [id]);

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (!trace) return <p className="text-sm text-zinc-500">loading…</p>;

  return (
    <div className="space-y-4">
      <div className="text-sm text-zinc-400">
        <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs">{trace.mode}</span>{" "}
        <span className="text-zinc-200">{trace.question}</span>
      </div>
      {trace.final_answer && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
          {trace.degraded && (
            <div className="mb-3 inline-block rounded bg-amber-950 px-2 py-0.5 text-xs text-amber-300">
              degraded
            </div>
          )}
          <div className="prose-answer">
            <ReactMarkdown>{trace.final_answer}</ReactMarkdown>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800 pt-3 text-xs text-zinc-500 tabular-nums">
            <span>
              {trace.totals.model_calls} calls ({trace.totals.api_attempts} API attempts) ·{" "}
              {trace.totals.input_tokens + trace.totals.output_tokens} tok · $
              {trace.totals.cost_usd.toFixed(4)} ·{" "}
              {((trace.totals.latency_ms ?? 0) / 1000).toFixed(1)}s
            </span>
            <Rating requestId={trace.id} initial={trace.user_rating} />
          </div>
        </div>
      )}
      {trace.status === "failed" && (
        <div className="rounded-lg border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
          failed: {trace.error}
        </div>
      )}
      <TraceView trace={trace} />
    </div>
  );
}
