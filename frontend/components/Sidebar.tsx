"use client";

import { API } from "@/lib/api";

export type ConversationItem = {
  id: string;
  title: string;
  pinned: boolean;
  updated_at: string;
};

export default function Sidebar({
  items,
  activeId,
  onNew,
  onSelect,
  onTogglePin,
}: {
  items: ConversationItem[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onTogglePin: (item: ConversationItem) => void;
}) {
  const pinned = items.filter((i) => i.pinned);
  const recent = items.filter((i) => !i.pinned);

  const Row = ({ item }: { item: ConversationItem }) => (
    <div
      className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm ${
        item.id === activeId
          ? "bg-zinc-800 text-zinc-100"
          : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
      }`}
    >
      <button
        onClick={() => onSelect(item.id)}
        className="min-w-0 flex-1 truncate text-left"
        title={item.title}
      >
        {item.title}
      </button>
      <button
        onClick={() => onTogglePin(item)}
        className={`shrink-0 text-xs ${
          item.pinned
            ? "text-amber-400"
            : "text-zinc-700 opacity-0 hover:text-zinc-400 group-hover:opacity-100"
        }`}
        title={item.pinned ? "unpin" : "pin"}
      >
        {item.pinned ? "📌" : "📍"}
      </button>
    </div>
  );

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-zinc-800 md:flex">
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-200 hover:border-zinc-500 hover:bg-zinc-900"
        >
          + New chat
        </button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-2 pb-4">
        {pinned.length > 0 && (
          <div>
            <p className="px-2 pb-1 text-[11px] uppercase tracking-wide text-zinc-600">Pinned</p>
            {pinned.map((i) => (
              <Row key={i.id} item={i} />
            ))}
          </div>
        )}
        <div>
          <p className="px-2 pb-1 text-[11px] uppercase tracking-wide text-zinc-600">Recents</p>
          {recent.map((i) => (
            <Row key={i.id} item={i} />
          ))}
          {items.length === 0 && (
            <p className="px-2 text-xs text-zinc-700">no chats yet</p>
          )}
        </div>
      </div>
    </aside>
  );
}

export async function fetchConversations(): Promise<ConversationItem[]> {
  const r = await fetch(`${API}/conversations`);
  if (!r.ok) return [];
  return (await r.json()).items;
}

export async function togglePin(item: ConversationItem): Promise<void> {
  await fetch(`${API}/conversations/${item.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned: !item.pinned }),
  });
}
