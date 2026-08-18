"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { API, StageEvent, Trace, askAsync, getTrace } from "@/lib/api";
import PipelineView from "@/components/PipelineView";
import Rating from "@/components/Rating";
import TraceView from "@/components/TraceView";

const MODES = [
  { id: "quick", label: "Quick", hint: "one model, ~1¢" },
  { id: "council", label: "Council", hint: "both models + judge, ~2–5¢" },
  { id: "deep", label: "Deep", hint: "critique + verify, ~5–15¢" },
] as const;

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<string>("council");
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  async function submit() {
    if (!question.trim() || running) return;
    setRunning(true);
    setTrace(null);
    setEvents([]);
    setError(null);
    try {
      const id = await askAsync(question.trim(), mode);
      const es = new EventSource(`${API}/requests/${id}/stream`);
      esRef.current = es;
      es.onmessage = async (msg) => {
        const event: StageEvent = JSON.parse(msg.data);
        setEvents((prev) => [...prev, event]);
        if (event.type === "done") {
          es.close();
          setTrace(await getTrace(id));
          setRunning(false);
          if (event.status === "failed") setError(event.error ?? "request failed");
        }
      };
      es.onerror = () => {
        es.close();
        setRunning(false);
        setError("lost connection to the API — is it running?");
      };
    } catch (e) {
      setRunning(false);
      setError(String(e));
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          placeholder="Ask the council anything… (Ctrl+Enter to send)"
          rows={3}
          className="w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[15px] outline-none placeholder:text-zinc-600 focus:border-zinc-600"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {MODES.map((m) => (
            <button
              key={m.id}
              onClick={() => setMode(m.id)}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                mode === m.id
                  ? "border-sky-700 bg-sky-950 text-sky-200"
                  : "border-zinc-800 text-zinc-400 hover:border-zinc-600"
              }`}
              title={m.hint}
            >
              {m.label}
            </button>
          ))}
          <button
            onClick={submit}
            disabled={running || !question.trim()}
            className="ml-auto rounded-lg bg-sky-600 px-5 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
          >
            {running ? "Running…" : "Ask"}
          </button>
        </div>
      </div>

      {(events.length > 0 || running) && (
        <PipelineView events={events} running={running} />
      )}

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {trace?.final_answer && (
        <div className="space-y-4">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
            {trace.degraded && (
              <div className="mb-3 inline-block rounded bg-amber-950 px-2 py-0.5 text-xs text-amber-300">
                degraded — one provider or stage failed; see trace
              </div>
            )}
            <div className="prose-answer">
              <ReactMarkdown>{trace.final_answer}</ReactMarkdown>
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800 pt-3 text-xs text-zinc-500 tabular-nums">
              <span>
                {trace.mode} · {trace.totals.model_calls} calls ·{" "}
                {trace.totals.input_tokens + trace.totals.output_tokens} tok · $
                {trace.totals.cost_usd.toFixed(4)} ·{" "}
                {((trace.totals.latency_ms ?? 0) / 1000).toFixed(1)}s
              </span>
              <Rating requestId={trace.id} initial={trace.user_rating} />
            </div>
          </div>
          <TraceView trace={trace} />
        </div>
      )}
    </div>
  );
}
