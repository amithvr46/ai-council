"use client";

import { useState } from "react";
import { rate } from "@/lib/api";

export default function Rating({
  requestId,
  initial,
}: {
  requestId: string;
  initial: number | null;
}) {
  const [value, setValue] = useState<number | null>(initial);

  return (
    <div className="flex items-center gap-1 text-sm">
      <span className="mr-1 text-xs text-zinc-500">rate:</span>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          onClick={() => {
            setValue(n);
            rate(requestId, n);
          }}
          className={`px-1 text-lg leading-none ${
            value !== null && n <= value ? "text-amber-400" : "text-zinc-700 hover:text-zinc-500"
          }`}
          aria-label={`rate ${n}`}
        >
          ★
        </button>
      ))}
    </div>
  );
}
