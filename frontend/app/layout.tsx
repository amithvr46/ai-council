import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import StatsBar from "@/components/StatsBar";

export const metadata: Metadata = {
  title: "AI Council",
  description: "GPT + Claude. One verified answer.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen" suppressHydrationWarning>
        <header className="border-b border-zinc-800">
          <div className="mx-auto max-w-4xl px-4 py-3 flex items-center gap-6">
            <Link href="/" className="font-semibold tracking-tight text-zinc-100">
              AI&nbsp;Council
            </Link>
            <nav className="flex gap-4 text-sm text-zinc-400">
              <Link href="/" className="hover:text-zinc-100">Ask</Link>
              <Link href="/history" className="hover:text-zinc-100">History</Link>
            </nav>
            <div className="ml-auto">
              <StatsBar />
            </div>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
