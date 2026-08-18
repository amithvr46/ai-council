"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { Step, Trace } from "@/lib/api";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-zinc-800">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-900"
      >
        <span>{title}</span>
        <span className="text-zinc-600">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="border-t border-zinc-800 px-4 py-3">{children}</div>}
    </div>
  );
}

function stepText(step: Step): string {
  const o = step.output as Record<string, unknown> | null;
  if (!o) return "";
  if (typeof o.text === "string") return o.text;
  return "";
}

const CLASS_COLORS: Record<string, string> = {
  SUPPORTED: "text-emerald-400",
  INFERRED: "text-sky-400",
  UNSUPPORTED: "text-amber-400",
  CONTRADICTED: "text-red-400",
};

export default function TraceView({ trace }: { trace: Trace }) {
  const byStage = (name: string) => trace.steps.find((s) => s.stage === name);
  const candA = byStage("candidate_a");
  const candB = byStage("candidate_b");
  const check = byStage("combined_check")?.output as any;
  const judge = byStage("judge")?.output as any;
  const verifier = byStage("verifier")?.output as any;
  const revision = byStage("revision")?.output as any;

  return (
    <div className="space-y-2">
      {candA && candB && (
        <>
          <Section title={`Candidate A · ${candA.provider} · $${candA.cost_usd.toFixed(4)}`}>
            <div className="prose-answer text-zinc-300">
              <ReactMarkdown>{stepText(candA)}</ReactMarkdown>
            </div>
          </Section>
          <Section title={`Candidate B · ${candB.provider} · $${candB.cost_usd.toFixed(4)}`}>
            <div className="prose-answer text-zinc-300">
              <ReactMarkdown>{stepText(candB)}</ReactMarkdown>
            </div>
          </Section>
        </>
      )}

      {check && (
        <Section
          title={`Agreement check — ${check.agreement}${
            check.disagreement_type && check.disagreement_type !== "none"
              ? ` (${check.disagreement_type})`
              : ""
          }`}
        >
          <p className="text-sm text-zinc-300">{check.summary}</p>
          {check.key_disagreements?.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-sm text-zinc-400">
              {check.key_disagreements.map((d: string, i: number) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {judge && (
        <Section title={`Judge — ${judge.decision} (${judge.confidence} confidence)`}>
          <p className="mb-3 text-sm text-zinc-300">{judge.rationale}</p>
          <table className="w-full text-sm">
            <tbody>
              {judge.dimensions?.map((d: any, i: number) => (
                <tr key={i} className="border-t border-zinc-800">
                  <td className="py-1.5 pr-2 text-zinc-400">{d.dimension}</td>
                  <td className="py-1.5 pr-2 font-medium text-zinc-200">{d.winner}</td>
                  <td className="py-1.5 text-zinc-500">{d.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {verifier && (
        <Section title={`Verifier — ${String(verifier.verdict).toUpperCase()}`}>
          {verifier.claims?.length > 0 && (
            <ul className="space-y-1.5 text-sm">
              {verifier.claims.map((c: any, i: number) => (
                <li key={i}>
                  <span className={`font-mono text-xs ${CLASS_COLORS[c.classification] ?? ""}`}>
                    [{c.classification}]
                  </span>{" "}
                  <span className="text-zinc-300">{c.claim}</span>
                </li>
              ))}
            </ul>
          )}
          {verifier.reasons?.length > 0 && (
            <div className="mt-3 text-sm text-amber-300">
              {verifier.reasons.map((r: string, i: number) => (
                <p key={i}>→ {r}</p>
              ))}
            </div>
          )}
        </Section>
      )}

      {revision && (
        <Section title="Revision — changes made">
          <ul className="list-disc pl-5 text-sm text-zinc-400">
            {revision.changes?.map((c: string, i: number) => <li key={i}>{c}</li>)}
          </ul>
        </Section>
      )}

      <Section title="Raw execution log">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-left text-zinc-500">
              <th className="py-1 pr-2">#</th>
              <th className="py-1 pr-2">stage</th>
              <th className="py-1 pr-2">provider</th>
              <th className="py-1 pr-2">model</th>
              <th className="py-1 pr-2">prompt</th>
              <th className="py-1 pr-2">tok in/out</th>
              <th className="py-1 pr-2">cost</th>
              <th className="py-1 pr-2">ms</th>
              <th className="py-1">status</th>
            </tr>
          </thead>
          <tbody>
            {trace.steps.map((s) => (
              <tr key={s.seq} className="border-t border-zinc-900 text-zinc-400">
                <td className="py-1 pr-2">{s.seq}</td>
                <td className="py-1 pr-2 text-zinc-300">{s.stage}</td>
                <td className="py-1 pr-2">{s.provider ?? "—"}</td>
                <td className="py-1 pr-2">{s.model ?? "—"}</td>
                <td className="py-1 pr-2">{s.prompt_version ?? "—"}</td>
                <td className="py-1 pr-2">
                  {s.tokens.input}/{s.tokens.output}
                </td>
                <td className="py-1 pr-2">${s.cost_usd.toFixed(4)}</td>
                <td className="py-1 pr-2">{s.latency_ms ?? "—"}</td>
                <td className={`py-1 ${s.status === "error" ? "text-red-400" : ""}`}>
                  {s.status}
                  {s.api_attempts > 1 ? ` (${s.api_attempts} attempts)` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
