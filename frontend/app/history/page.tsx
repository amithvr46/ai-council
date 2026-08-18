"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API } from "@/lib/api";

type Item = {
  id: string;
  created_at: string;
  question: string;
  mode: string;
  status: string;
  degraded: boolean;
  cost_usd: number;
  latency_ms: number | null;
  user_rating: number | null;
};

export default function HistoryPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const limit = 25;

  useEffect(() => {
    fetch(`${API}/requests?limit=${limit}&offset=${offset}`)
      .then((r) => r.json())
      .then((d) => {
        setItems(d.items);
        setTotal(d.total);
      })
      .catch(() => {});
  }, [offset]);

  return (
    <div className="mx-auto max-w-4xl space-y-3 px-4 py-6">
      <h1 className="text-lg font-semibold">History</h1>
      <div className="divide-y divide-zinc-900 rounded-xl border border-zinc-800">
        {items.map((it) => (
          <Link
            key={it.id}
            href={`/history/${it.id}`}
            className="flex items-center gap-3 px-4 py-3 hover:bg-zinc-900/60"
          >
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] ${
                it.mode === "deep"
                  ? "bg-purple-950 text-purple-300"
                  : it.mode === "council"
                    ? "bg-sky-950 text-sky-300"
                    : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {it.mode}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-zinc-200">{it.question}</span>
            {it.degraded && <span className="text-xs text-amber-400">degraded</span>}
            {it.status === "failed" && <span className="text-xs text-red-400">failed</span>}
            {it.user_rating && (
              <span className="text-xs text-amber-400">{"★".repeat(it.user_rating)}</span>
            )}
            <span className="text-xs tabular-nums text-zinc-500">
              ${it.cost_usd.toFixed(3)}
            </span>
            <span className="w-24 text-right text-xs tabular-nums text-zinc-600">
              {new Date(it.created_at).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </Link>
        ))}
        {items.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-zinc-600">no requests yet</p>
        )}
      </div>
      {total > limit && (
        <div className="flex justify-between text-sm text-zinc-400">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - limit))}
            className="disabled:opacity-30"
          >
            ← newer
          </button>
          <span className="text-xs text-zinc-600">
            {offset + 1}–{Math.min(offset + limit, total)} of {total}
          </span>
          <button
            disabled={offset + limit >= total}
            onClick={() => setOffset(offset + limit)}
            className="disabled:opacity-30"
          >
            older →
          </button>
        </div>
      )}
    </div>
  );
}
