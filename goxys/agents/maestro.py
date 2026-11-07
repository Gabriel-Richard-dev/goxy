"""Maestro (orquestrador): fachada estável sobre o grafo do LangGraph (`agents/grafo.py`).

- `handle(case_id)`: roda o grafo do caso (thread_id = case_id, checkpoint em data/checkpoints.db).
- `resume(aprovacao_id, decisao)`: retoma o `interrupt()` da aprovação com `Command(resume=...)`.
- `registrar_listener()`: liga `approval.decided` do barramento ao `resume`.

O plano, a timeline e os eventos são os mesmos de antes; a diferença é a durabilidade: uma aprovação
pendente sobrevive a um reinício do servidor.
"""
from __future__ import annotations

from core.bus import bus

from . import grafo

_registrado = False


async def handle(case_id: str) -> None:
    await grafo.iniciar(case_id)


async def resume(aprovacao_id: str, decisao, *, case_id: str | None = None) -> None:
    info = decisao if isinstance(decisao, dict) else {"decisao": decisao}
    await grafo.retomar_aprovacao(aprovacao_id, info, case_id)


async def _on_event(evt: dict) -> None:
    if evt.get("type") == "approval.decided":
        data = evt.get("data") or {}
        if data.get("aprovacao_id"):
            await resume(data["aprovacao_id"], data, case_id=evt.get("case_id"))


def registrar_listener() -> None:
    global _registrado
    if not _registrado:
        bus.subscribe_listener(_on_event)
        _registrado = True
