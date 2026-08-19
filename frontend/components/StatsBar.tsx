"use client";

import { useEffect, useState } from "react";
import { API } from "@/lib/api";

type Stats = {
  today: { requests: number; cost_usd: number };
  month: { requests: number; cost_usd: number; avg_latency_ms: number | null };
};

type Budget = {
  settings: { daily_limit_usd: number; warn_threshold_pct: number };
  spent_today: number;
  remaining_today: number | null;
  warn_at_today: number;
};

export default function StatsBar() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API}/stats`).then((r) => r.json()).then(setStats).catch(() => {});
      fetch(`${API}/budget`).then((r) => r.json()).then(setBudget).catch(() => {});
    };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  if (!stats) return null;

  // Amber once today's spend has crossed the warn threshold.
  const warned =
    budget !== null &&
    budget.settings.daily_limit_usd > 0 &&
    budget.spent_today >= budget.warn_at_today;

  return (
    <div className="text-xs text-zinc-500 tabular-nums">
      today <span className="text-zinc-300">{stats.today.requests}</span> req ·{" "}
      <span className="text-zinc-300">${stats.today.cost_usd.toFixed(2)}</span>
      {budget?.remaining_today !== null && budget !== null && (
        <>
          <span className="mx-2 text-zinc-700">|</span>
          <span className={warned ? "text-amber-400" : "text-zinc-500"} title={
            `daily limit $${budget.settings.daily_limit_usd.toFixed(2)}`
          }>
            ${budget.remaining_today!.toFixed(2)} left today
          </span>
        </>
      )}
      <span className="mx-2 text-zinc-700">|</span>
      month <span className="text-zinc-300">${stats.month.cost_usd.toFixed(2)}</span>
    </div>
  );
}
