export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type StageEvent = {
  type: "started" | "stage" | "done";
  stage?: string;
  status?: string;
  provider?: string;
  model?: string;
  cost_usd?: number;
  latency_ms?: number;
  degraded?: boolean;
  error?: string | null;
  mode?: string;
};

export type Step = {
  seq: number;
  stage: string;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  status: string;
  error: string | null;
  output: Record<string, unknown> | null;
  tokens: { input: number; output: number };
  cost_usd: number;
  latency_ms: number | null;
  api_attempts: number;
};

export type Trace = {
  id: string;
  question: string;
  mode: string;
  status: string;
  final_answer: string | null;
  degraded: boolean;
  error: string | null;
  totals: {
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    model_calls: number;
    api_attempts: number;
    latency_ms: number | null;
  };
  user_rating: number | null;
  steps: Step[];
};

export async function askAsync(question: string, mode: string): Promise<string> {
  const r = await fetch(`${API}/ask/async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, mode }),
  });
  if (!r.ok) throw new Error(`ask failed: ${r.status}`);
  return (await r.json()).id;
}

export async function getTrace(id: string): Promise<Trace> {
  const r = await fetch(`${API}/requests/${id}`);
  if (!r.ok) throw new Error(`trace failed: ${r.status}`);
  return r.json();
}

export async function rate(id: string, rating: number): Promise<void> {
  await fetch(`${API}/requests/${id}/rating`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
}
