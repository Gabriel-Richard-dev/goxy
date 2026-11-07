"""Barramento de eventos em memória. Alimenta o SSE do painel, os canais e o log estruturado.

Todo evento tem o formato:
    {"id": int, "type": str, "ts": iso, "case_id": str|None, "agent": str|None, "data": dict}
Os tipos estão documentados em CONTRACTS.md.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable

log = logging.getLogger("goxys.bus")

Listener = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self, history: int = 5000) -> None:
        self._seq = itertools.count(1)
        self._history: list[dict] = []
        self._max = history
        self._queues: set[asyncio.Queue] = set()
        self._listeners: list[Listener] = []

    def subscribe_listener(self, fn: Listener) -> None:
        """Ouvinte assíncrono in-process (ex.: canais Slack/Telegram)."""
        self._listeners.append(fn)

    async def publish(self, type: str, data: dict | None = None, *, case_id: str | None = None,
                      agent: str | None = None) -> dict:
        evt = {
            "id": next(self._seq),
            "type": type,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "case_id": case_id,
            "agent": agent,
            "data": data or {},
        }
        self._history.append(evt)
        if len(self._history) > self._max:
            self._history = self._history[-self._max:]
        log.info("event %s", json.dumps(evt, ensure_ascii=False, default=str)[:500])
        for q in list(self._queues):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
        for fn in self._listeners:
            asyncio.create_task(_safe(fn, evt))
        return evt

    def history(self, case_id: str | None = None, since_id: int = 0) -> list[dict]:
        return [e for e in self._history
                if e["id"] > since_id and (case_id is None or e["case_id"] == case_id)]

    async def stream(self, since_id: int = 0) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._queues.add(q)
        try:
            for e in self.history(since_id=since_id):
                yield e
            while True:
                yield await q.get()
        finally:
            self._queues.discard(q)


async def _safe(fn: Listener, evt: dict) -> None:
    try:
        await fn(evt)
    except Exception:  # um canal com problema nunca derruba o fluxo
        log.exception("listener falhou para %s", evt.get("type"))


bus = EventBus()


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
