"""Utilidades comuns dos agentes: passo vivo da linha do tempo, agente corrente, datas do ambiente.

Uso:
    async with passo(case, "porteiro", "lendo a mensagem") as s:
        ...
        s.titulo = "classificou como avaria"
        s.status = "ok"          # ou "alerta" / "falhou"; se ficar "rodando", vira "ok"
        s.evidencias.append({...})
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from typing import AsyncIterator

from core import config
from core.bus import bus
from core.state import Case, TimelineStep, now, store

log = logging.getLogger("goxys.agents")

# Agente que está agindo agora (as ferramentas usam para registrar "quem pediu")
agente_atual: contextvars.ContextVar[str] = contextvars.ContextVar("agente_atual", default="maestro")
# Passo vivo corrente (as ferramentas usam para narrar, ex.: "API indisponível → entrei no portal")
passo_atual: contextvars.ContextVar[TimelineStep | None] = contextvars.ContextVar("passo_atual", default=None)


def _achar_passo(case: Case, agent: str, novo: bool) -> TimelineStep:
    if not novo:
        for s in case.timeline:
            if s.agent == agent and s.status in ("pendente", "rodando"):
                return s
        for s in case.timeline:
            if s.agent == agent and s.status == "pendente":
                return s
    s = TimelineStep(agent=agent)
    case.timeline.append(s)
    return s


async def abrir_passo(case: Case, agent: str, titulo: str, *, novo: bool = False) -> TimelineStep:
    """Abre um passo `rodando` que será fechado por outro nó do grafo (ex.: Detetive prep → Detetive)."""
    s = _achar_passo(case, agent, novo)
    s.status, s.titulo, s.detalhe, s.evidencias, s.inicio, s.fim = "rodando", titulo, "", [], now(), None
    await bus.publish("agent.started", {"titulo": titulo}, case_id=case.id, agent=agent)
    await store.save(case)
    return s


async def fechar_passo(case: Case, s: TimelineStep, *, atraso: bool = True) -> None:
    if s.status == "rodando":
        s.status = "ok"
    s.fim = now()
    try:
        s.duracao_ms = int((datetime.fromisoformat(s.fim) - datetime.fromisoformat(s.inicio)).total_seconds() * 1000)
    except (TypeError, ValueError):
        s.duracao_ms = None
    await bus.publish("agent.finished", {"status": s.status, "titulo": s.titulo, "detalhe": s.detalhe,
                                         "evidencias": s.evidencias, "duracao_ms": s.duracao_ms},
                      case_id=case.id, agent=s.agent)
    await store.save(case)
    if atraso and config.DEMO_STEP_DELAY_S > 0:
        await asyncio.sleep(config.DEMO_STEP_DELAY_S)


@contextmanager
def como(agent: str, s: TimelineStep | None = None):
    """Define o agente corrente (e o passo vivo) para as ferramentas registrarem quem pediu."""
    tok_a = agente_atual.set(agent)
    tok_p = passo_atual.set(s)
    try:
        yield
    finally:
        agente_atual.reset(tok_a)
        passo_atual.reset(tok_p)


@asynccontextmanager
async def passo(case: Case, agent: str, titulo: str, *, novo: bool = False,
                atraso: bool = True) -> AsyncIterator[TimelineStep]:
    """Abre um passo `rodando`, publica agent.started e, no fim, agent.finished.

    `novo=True` cria outro passo do mesmo agente (ex.: Voz manda duas mensagens no caso).
    """
    s = _achar_passo(case, agent, novo)
    s.status = "rodando"
    s.titulo = titulo
    s.detalhe = ""
    s.evidencias = []
    s.inicio = now()
    s.fim = None
    t0 = time.perf_counter()
    tok_a = agente_atual.set(agent)
    tok_p = passo_atual.set(s)
    await bus.publish("agent.started", {"titulo": titulo}, case_id=case.id, agent=agent)
    await store.save(case)
    try:
        yield s
        if s.status == "rodando":
            s.status = "ok"
    except Exception as e:  # o passo falha, o Maestro decide o que fazer
        log.exception("agente %s falhou no caso %s", agent, case.id)
        s.status = "falhou"
        s.titulo = s.titulo if s.titulo != titulo else f"falhou: {titulo}"
        s.detalhe = (s.detalhe + "\n" if s.detalhe else "") + f"{type(e).__name__}: {e}"
        raise
    finally:
        s.fim = now()
        s.duracao_ms = int((time.perf_counter() - t0) * 1000)
        agente_atual.reset(tok_a)
        passo_atual.reset(tok_p)
        await bus.publish("agent.finished", {"status": s.status, "titulo": s.titulo, "detalhe": s.detalhe,
                                             "evidencias": s.evidencias, "duracao_ms": s.duracao_ms},
                          case_id=case.id, agent=agent)
        await store.save(case)
        if atraso and config.DEMO_STEP_DELAY_S > 0:
            await asyncio.sleep(config.DEMO_STEP_DELAY_S)


async def narrar(case: Case, titulo: str | None = None, detalhe: str | None = None,
                 evidencia: dict | None = None) -> None:
    """Atualiza o passo corrente ao vivo (a linha do tempo mostra o que o agente está fazendo agora)."""
    s = passo_atual.get()
    if s is None:
        return
    if titulo:
        s.titulo = titulo
    if detalhe:
        s.detalhe = (s.detalhe + "\n" if s.detalhe else "") + detalhe
    if evidencia:
        s.evidencias.append(evidencia)
    await store.save(case)


async def mascote(case: Case, estado: str) -> None:
    case.mascot_state = estado  # type: ignore[assignment]
    await bus.publish("mascot.state", {"state": estado}, case_id=case.id)
    await store.save(case)


# ---------- datas do ambiente ("agora" = 2026-11-02 08:00) ----------

FERIADOS = {date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15),
            date(2026, 11, 20), date(2026, 12, 25), date(2027, 1, 1)}


def agora_ambiente() -> datetime:
    return datetime.fromisoformat(config.NOW_ISO)


def parse_data(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).strip().replace("Z", ""))
    except ValueError:
        try:
            return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None


def dias_uteis_entre(inicio: date, fim: date) -> int:
    """Dias úteis em (inicio, fim]. Negativo se fim < inicio."""
    if fim < inicio:
        return -dias_uteis_entre(fim, inicio)
    n, d = 0, inicio
    while d < fim:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in FERIADOS:
            n += 1
    return n


def brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "R$ ?"
