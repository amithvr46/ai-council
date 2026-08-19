"use client";

import { useState } from "react";

import {
  ResumeResult,
  downloadUrl,
  generateResume,
  uploadDocument,
} from "@/lib/api";

type Uploaded = { id: string; title: string; char_count: number };

export default function ResumePage() {
  const [sources, setSources] = useState<Uploaded[]>([]);
  const [jd, setJd] = useState<Uploaded | null>(null);
  const [jdText, setJdText] = useState("");
  const [instruction, setInstruction] = useState("");
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ResumeResult | null>(null);

  async function upload(file: File | undefined, authority: string) {
    if (!file) return;
    setError(null);
    setBusy(`Reading ${file.name}…`);
    try {
      const doc = await uploadDocument(file, authority, file.name);
      if (authority === "jd") setJd(doc);
      else setSources((prev) => [...prev.filter((d) => d.id !== doc.id), doc]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(null);
    }
  }

  async function generate() {
    setError(null);
    setResult(null);
    setBusy("Reading the job description, selecting relevant experience, writing, reviewing…");
    try {
      setResult(
        await generateResume({
          jd_document_id: jd?.id,
          jd_text: jd ? "" : jdText,
          instruction,
          name,
          contact,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "generation failed");
    } finally {
      setBusy(null);
    }
  }

  const ready = Boolean(jd || jdText.trim());

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 text-zinc-200">
      <h1 className="text-xl font-medium">Tailor a resume</h1>
      <p className="mt-1 text-sm text-zinc-500">
        Your career sources establish what is true. The job description only decides
        what to emphasise. Nothing it asks for that you have not done will appear.
      </p>

      <section className="mt-8 space-y-6">
        <Field label="1 · Career sources" hint="Master resume, or any document describing your work. These stay on this machine.">
          <input
            type="file"
            className="text-sm"
            onChange={(e) => upload(e.target.files?.[0], "master_resume")}
          />
          {sources.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-zinc-400">
              {sources.map((d) => (
                <li key={d.id}>✓ {d.title} · {d.char_count.toLocaleString()} chars</li>
              ))}
            </ul>
          )}
        </Field>

        <Field label="2 · Job description" hint="Upload a file, or paste the text.">
          <input
            type="file"
            className="text-sm"
            onChange={(e) => upload(e.target.files?.[0], "jd")}
          />
          {jd ? (
            <p className="mt-2 text-xs text-zinc-400">✓ {jd.title}</p>
          ) : (
            <textarea
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              rows={5}
              placeholder="…or paste the job description here"
              className="mt-2 w-full rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 text-sm outline-none focus:border-zinc-600"
            />
          )}
        </Field>

        <Field
          label="3 · Anything else (optional)"
          hint="Plain English. Tell it what you have done, or how you want it presented."
        >
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={3}
            placeholder="I also have professional Harness experience. Emphasise AKS and production troubleshooting and keep it to 2 pages."
            className="w-full rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 text-sm outline-none focus:border-zinc-600"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm outline-none focus:border-zinc-600"
          />
          <input
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="City | phone | email | linkedin"
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm outline-none focus:border-zinc-600"
          />
        </div>

        <button
          onClick={generate}
          disabled={!ready || busy !== null}
          className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
        >
          {busy ? "Working…" : "Generate resume"}
        </button>

        {busy && <p className="text-sm text-zinc-400">{busy}</p>}
        {error && (
          <p className="rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}
      </section>

      {result && <Result result={result} />}
    </main>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h2 className="text-sm font-medium text-zinc-300">{label}</h2>
      <p className="mb-2 text-xs text-zinc-500">{hint}</p>
      {children}
    </div>
  );
}

function Result({ result }: { result: ResumeResult }) {
  const [details, setDetails] = useState(false);
  return (
    <section className="mt-10 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-medium">Your resume is ready</h2>
          <p className="mt-1 text-xs text-zinc-500 tabular-nums">
            {result.role_family.replace("_", " ")} · match {result.match_quality} ·{" "}
            {result.model_calls} calls · ${result.cost_usd.toFixed(4)}
          </p>
        </div>
        <a
          href={downloadUrl(result.download_url)}
          className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900"
        >
          Download DOCX
        </a>
      </div>

      {result.gaps.length > 0 && (
        <p className="mt-4 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          This role asks for {result.gaps.join(", ")}, which none of your career sources
          establish. It was not claimed. Everything else was written from what you have
          actually done.
        </p>
      )}

      <button
        onClick={() => setDetails((d) => !d)}
        className="mt-4 text-xs text-zinc-400 underline decoration-zinc-700 hover:text-zinc-200"
      >
        {details ? "hide details" : "how this was built"}
      </button>

      {details && (
        <div className="mt-3 space-y-2 text-xs text-zinc-400">
          {result.instruction.career_statements.length > 0 && (
            <p>
              <span className="text-zinc-500">Taken as your experience: </span>
              {result.instruction.career_statements.join(" ")}
            </p>
          )}
          {result.instruction.preferences.length > 0 && (
            <p>
              <span className="text-zinc-500">Applied to this resume only: </span>
              {result.instruction.preferences.join(" ")}
            </p>
          )}
          <p>
            <span className="text-zinc-500">Unresolved checks: </span>
            {result.findings.length === 0 ? "none" : result.findings.length}
          </p>
          {result.would_submit === false && (
            <p className="text-amber-300">
              The internal review would not submit this as-is — usually a coverage gap
              rather than a problem with the writing.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
