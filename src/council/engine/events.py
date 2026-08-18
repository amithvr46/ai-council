"""In-process pub/sub for live pipeline progress.

The engine publishes one event per persisted stage; the SSE endpoint
subscribes per request id. Single-process by design for V1 — if the API
ever runs multi-worker, this swaps for Postgres LISTEN/NOTIFY or Redis
behind the same interface.
"""

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def publish(self, request_id: str, event: dict) -> None:
        for q in self._subs.get(request_id, []):
            q.put_nowait(event)

    def subscribe(self, request_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[request_id].append(q)
        return q

    def unsubscribe(self, request_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(request_id)
        if subs and q in subs:
            subs.remove(q)
        if subs is not None and not subs:
            del self._subs[request_id]


bus = EventBus()
