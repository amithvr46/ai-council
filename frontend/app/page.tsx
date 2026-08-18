"use client";

import { useEffect, useRef, useState } from "react";
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

type ChatEntry = {
  key: number;
  question: string;
  mode: string;
  events: StageEvent[];
  trace: Trace | null;
  running: boolean;
  error: string | null;
  showTrace: boolean;
};

export default function AskPage() {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<string>("council");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const keyRef = useRef(0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries]);

  function patch(key: number, update: Partial<ChatEntry>) {
    setEntries((prev) => prev.map((e) => (e.key === key ? { ...e, ...update } : e)));
  }

  async function submit() {
    const question = input.trim();
    if (!question || entries.some((e) => e.running)) return;
    const key = keyRef.current++;
    setInput("");
    setEntries((prev) => [
      ...prev,
      { key, question, mode, events: [], trace: null, running: true, error: null, showTrace: false },
    ]);
    try {
      const id = await askAsync(question, mode);
      const es = new EventSource(`${API}/requests/${id}/stream`);
      es.onmessage = async (msg) => {
        const event: StageEvent = JSON.parse(msg.data);
        setEntries((prev) =>
          prev.map((e) => (e.key === key ? { ...e, events: [...e.events, event] } : e))
        );
        if (event.type === "done") {
          es.close();
          const trace = await getTrace(id);
          patch(key, {
            trace,
            running: false,
            error: event.status === "failed" ? (event.error ?? "request failed") : null,
          });
        }
      };
      es.onerror = () => {
        es.close();
        patch(key, { running: false, error: "lost connection to the API — is it running?" });
      };
    } catch (e) {
      patch(key, { running: false, error: String(e) });
    }
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {/* conversation */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {entries.length === 0 && (
            <div className="pt-24 text-center text-zinc-600">
              <p className="text-lg text-zinc-400">Ask the council anything.</p>
              <p className="mt-2 text-sm">
                GPT and Claude answer independently — you get one verified answer.
              </p>
            </div>
          )}

          {entries.map((e) => (
            <div key={e.key} className="space-y-3">
              {/* user bubble */}
              <div className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-sky-900/60 px-4 py-2.5 text-[15px] text-zinc-100">
                  {e.question}
                </div>
              </div>

              {/* pipeline progress */}
              {(e.running || (e.events.length > 0 && !e.trace)) && (
                <PipelineView events={e.events} running={e.running} />
              )}

              {e.error && (
                <div className="rounded-lg border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
                  {e.error}
                </div>
              )}

              {/* answer */}
              {e.trace?.final_answer && (
                <div className="space-y-2">
                  <div className="rounded-2xl rounded-bl-sm border border-zinc-800 bg-zinc-900/40 px-5 py-4">
                    {e.trace.degraded && (
                      <div className="mb-3 inline-block rounded bg-amber-950 px-2 py-0.5 text-xs text-amber-300">
                        degraded — one provider or stage failed
                      </div>
                    )}
                    <div className="prose-answer">
                      <ReactMarkdown>{e.trace.final_answer}</ReactMarkdown>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/70 pt-2.5 text-xs text-zinc-500 tabular-nums">
                      <span>
                        {e.trace.mode} · {e.trace.totals.model_calls} calls · $
                        {e.trace.totals.cost_usd.toFixed(4)} ·{" "}
                        {((e.trace.totals.latency_ms ?? 0) / 1000).toFixed(1)}s
                        <button
                          onClick={() => patch(e.key, { showTrace: !e.showTrace })}
                          className="ml-3 text-zinc-400 underline decoration-zinc-700 hover:text-zinc-200"
                        >
                          {e.showTrace ? "hide details" : "how this was decided"}
                        </button>
                      </span>
                      <Rating requestId={e.trace.id} initial={e.trace.user_rating} />
                    </div>
                  </div>
                  {e.showTrace && <TraceView trace={e.trace} />}
                </div>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* composer pinned at the bottom */}
      <div className="border-t border-zinc-800 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto max-w-3xl px-4 py-3">
          <div className="rounded-2xl border border-zinc-700 bg-zinc-900 focus-within:border-zinc-500">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(ev) => setInput(ev.target.value)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" && !ev.shiftKey) {
                  ev.preventDefault();
                  submit();
                }
              }}
              placeholder="Ask the council… (Enter to send, Shift+Enter for a new line)"
              rows={2}
              className="w-full resize-none bg-transparent px-4 pt-3 text-[15px] outline-none placeholder:text-zinc-600"
            />
            <div className="flex items-center gap-1.5 px-3 pb-2.5">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMode(m.id)}
                  className={`rounded-full px-2.5 py-1 text-xs ${
                    mode === m.id
                      ? "bg-sky-950 text-sky-300 ring-1 ring-sky-800"
                      : "text-zinc-500 hover:text-zinc-300"
                  }`}
                  title={m.hint}
                >
                  {m.label}
                </button>
              ))}
              <span className="ml-1 text-[10px] text-zinc-700">Auto coming soon</span>
              <button
                onClick={submit}
                disabled={!input.trim() || entries.some((e) => e.running)}
                className="ml-auto rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
              >
                {entries.some((e) => e.running) ? "Thinking…" : "Ask"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
