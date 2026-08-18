"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  API,
  StageEvent,
  Trace,
  askAsync,
  getConversation,
  getTrace,
} from "@/lib/api";
import PipelineView from "@/components/PipelineView";
import Rating from "@/components/Rating";
import Sidebar, {
  ConversationItem,
  fetchConversations,
  togglePin,
} from "@/components/Sidebar";
import TraceView from "@/components/TraceView";

const MODES = [
  { id: "quick", label: "Quick", hint: "one model, ~1¢" },
  { id: "council", label: "Council", hint: "both models + judge, ~2–5¢" },
  { id: "deep", label: "Deep", hint: "critique + verify, ~5–15¢" },
] as const;

type ChatEntry = {
  key: number;
  requestId: string | null;
  question: string;
  mode: string;
  events: StageEvent[];
  running: boolean;
  error: string | null;
  answer: {
    final_answer: string;
    degraded: boolean;
    cost_usd: number;
    latency_ms: number | null;
    model_calls: number;
    user_rating: number | null;
  } | null;
  trace: Trace | null; // lazy-loaded for old turns
  showTrace: boolean;
  live: { stage: string; text: string } | null; // streaming answer in progress
};

export default function AskPage() {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<string>("council");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const keyRef = useRef(0);

  useEffect(() => {
    fetchConversations().then(setConversations);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries]);

  function patch(key: number, update: Partial<ChatEntry>) {
    setEntries((prev) => prev.map((e) => (e.key === key ? { ...e, ...update } : e)));
  }

  function newChat() {
    setConversationId(null);
    setEntries([]);
    setInput("");
  }

  async function openConversation(id: string) {
    const conv = await getConversation(id);
    setConversationId(conv.id);
    setEntries(
      conv.requests
        .filter((r) => r.final_answer)
        .map((r) => ({
          key: keyRef.current++,
          requestId: r.id,
          question: r.question,
          mode: r.mode,
          events: [],
          running: false,
          error: null,
          answer: {
            final_answer: r.final_answer!,
            degraded: r.degraded,
            cost_usd: r.cost_usd,
            latency_ms: r.latency_ms,
            model_calls: r.model_calls,
            user_rating: r.user_rating,
          },
          trace: null,
          showTrace: false,
          live: null,
        })),
    );
  }

  async function toggleTrace(entry: ChatEntry) {
    if (!entry.showTrace && !entry.trace && entry.requestId) {
      const trace = await getTrace(entry.requestId);
      patch(entry.key, { trace, showTrace: true });
    } else {
      patch(entry.key, { showTrace: !entry.showTrace });
    }
  }

  async function submit() {
    const question = input.trim();
    if (!question || entries.some((e) => e.running)) return;
    const key = keyRef.current++;
    setInput("");
    setEntries((prev) => [
      ...prev,
      {
        key,
        requestId: null,
        question,
        mode,
        events: [],
        running: true,
        error: null,
        answer: null,
        trace: null,
        showTrace: false,
        live: null,
      },
    ]);
    try {
      const { id, conversation_id } = await askAsync(question, mode, conversationId);
      setConversationId(conversation_id);
      patch(key, { requestId: id });
      const es = new EventSource(`${API}/requests/${id}/stream`);
      es.onmessage = async (msg) => {
        const event: StageEvent = JSON.parse(msg.data);
        if (event.type === "delta") {
          // Live answer text: reset the buffer when a new stage starts
          // writing (e.g. a revision replacing the judge's draft).
          setEntries((prev) =>
            prev.map((e) =>
              e.key === key
                ? {
                    ...e,
                    live:
                      e.live && e.live.stage === event.stage
                        ? { stage: e.live.stage, text: e.live.text + (event.text ?? "") }
                        : { stage: event.stage ?? "", text: event.text ?? "" },
                  }
                : e
            )
          );
          return;
        }
        setEntries((prev) =>
          prev.map((e) => (e.key === key ? { ...e, events: [...e.events, event] } : e))
        );
        if (event.type === "done") {
          es.close();
          const trace = await getTrace(id);
          patch(key, {
            running: false,
            live: null,
            trace,
            answer: trace.final_answer
              ? {
                  final_answer: trace.final_answer,
                  degraded: trace.degraded,
                  cost_usd: trace.totals.cost_usd,
                  latency_ms: trace.totals.latency_ms,
                  model_calls: trace.totals.model_calls,
                  user_rating: trace.user_rating,
                }
              : null,
            error: event.status === "failed" ? (event.error ?? "request failed") : null,
          });
          fetchConversations().then(setConversations);
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
    <div className="flex h-[calc(100vh-3.5rem)]">
      <Sidebar
        items={conversations}
        activeId={conversationId}
        onNew={newChat}
        onSelect={openConversation}
        onTogglePin={async (item) => {
          await togglePin(item);
          fetchConversations().then(setConversations);
        }}
      />

      <div className="flex min-w-0 flex-1 flex-col">
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
                <div className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-sky-900/60 px-4 py-2.5 text-[15px] text-zinc-100">
                    {e.question}
                  </div>
                </div>

                {(e.running || (e.events.length > 0 && !e.answer)) && (
                  <PipelineView events={e.events} running={e.running} />
                )}

                {e.live && !e.answer && (
                  <div className="rounded-2xl rounded-bl-sm border border-zinc-800 bg-zinc-900/40 px-5 py-4">
                    <div className="prose-answer">
                      <ReactMarkdown>{e.live.text}</ReactMarkdown>
                    </div>
                    <span className="mt-1 inline-block h-4 w-1.5 animate-pulse bg-sky-400 align-text-bottom" />
                  </div>
                )}

                {e.error && (
                  <div className="rounded-lg border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
                    {e.error}
                  </div>
                )}

                {e.answer && (
                  <div className="space-y-2">
                    <div className="rounded-2xl rounded-bl-sm border border-zinc-800 bg-zinc-900/40 px-5 py-4">
                      {e.answer.degraded && (
                        <div className="mb-3 inline-block rounded bg-amber-950 px-2 py-0.5 text-xs text-amber-300">
                          degraded — one provider or stage failed
                        </div>
                      )}
                      <div className="prose-answer">
                        <ReactMarkdown>{e.answer.final_answer}</ReactMarkdown>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/70 pt-2.5 text-xs text-zinc-500 tabular-nums">
                        <span>
                          {e.mode} · {e.answer.model_calls} calls · $
                          {e.answer.cost_usd.toFixed(4)} ·{" "}
                          {((e.answer.latency_ms ?? 0) / 1000).toFixed(1)}s
                          <button
                            onClick={() => toggleTrace(e)}
                            className="ml-3 text-zinc-400 underline decoration-zinc-700 hover:text-zinc-200"
                          >
                            {e.showTrace ? "hide details" : "how this was decided"}
                          </button>
                        </span>
                        {e.requestId && (
                          <Rating requestId={e.requestId} initial={e.answer.user_rating} />
                        )}
                      </div>
                    </div>
                    {e.showTrace && e.trace && <TraceView trace={e.trace} />}
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
    </div>
  );
}
