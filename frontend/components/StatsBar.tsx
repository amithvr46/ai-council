"use client";

import { useEffect, useState } from "react";
import { API } from "@/lib/api";

type Stats = {
  today: { requests: number; cost_usd: number };
  month: { requests: number; cost_usd: number; avg_latency_ms: number | null };
};

export default function StatsBar() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    const load = () =>
      fetch(`${API}/stats`)
        .then((r) => r.json())
        .then(setStats)
        .catch(() => {});
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  if (!stats) return null;
  return (
    <div className="text-xs text-zinc-500 tabular-nums">
      today <span className="text-zinc-300">{stats.today.requests}</span> req ·{" "}
      <span className="text-zinc-300">${stats.today.cost_usd.toFixed(2)}</span>
      <span className="mx-2 text-zinc-700">|</span>
      month <span className="text-zinc-300">${stats.month.cost_usd.toFixed(2)}</span>
    </div>
  );
}
