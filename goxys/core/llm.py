"""Cliente do AI Proxy do evento (formato OpenAI) + Jev (/v1/systemone) para decisões tipadas.

Regras: nunca usar deepseek-*; registrar tokens/custo de toda chamada (evento `llm.call`);
nunca logar o token. Sem AI_PROXY_API_KEY, `enabled()` é False e os agentes usam regras.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from . import config
from .bus import bus

log = logging.getLogger("goxys.llm")

SESSION = {"chamadas": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0}


def enabled() -> bool:
    return config.llm_enabled()


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.AI_PROXY_API_KEY}", "Content-Type": "application/json"}


def _guard_model(model: str) -> str:
    if model.lower().startswith("deepseek"):
        raise ValueError("deepseek-* está bloqueado no evento")
    return model


async def _account(case, agent: str, model: str, tin: int, tout: int, usd: float, ms: int, kind: str) -> None:
    SESSION["chamadas"] += 1
    SESSION["tokens_in"] += tin
    SESSION["tokens_out"] += tout
    SESSION["usd"] += usd
    if case is not None:
        u = case.llm_uso
        u["chamadas"] += 1
        u["tokens_in"] += tin
        u["tokens_out"] += tout
        u["usd"] = round(u["usd"] + usd, 7)
    log.info("💲 %s · %s · entrada %s tok · saída %s tok · %s | sessão: %s chamadas, %s tok",
             agent, model, tin, tout, f"US$ {usd:.7f}" if usd else "sem preço por chamada",
             SESSION["chamadas"], SESSION["tokens_in"] + SESSION["tokens_out"])
    await bus.publish("llm.call", {"kind": kind, "model": model, "tokens_in": tin, "tokens_out": tout,
                                   "usd": usd, "ms": ms, "sessao": dict(SESSION)},
                      case_id=getattr(case, "id", None), agent=agent)


async def chat(messages: list[dict], *, case=None, agent: str = "goxys", model: str | None = None,
               max_tokens: int = 2000, temperature: float = 0.3, json_mode: bool = False) -> str:
    """Retorna o texto da resposta. Lança exceção em falha (o agente decide o fallback)."""
    model = _guard_model(model or config.LLM_MODEL)
    body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                            "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as cli:
        r = await cli.post(f"{config.AI_PROXY_BASE_URL}/chat/completions", headers=_headers(), json=body)
        if r.status_code == 400 and json_mode:      # modelo sem response_format: tenta sem
            body.pop("response_format", None)
            r = await cli.post(f"{config.AI_PROXY_BASE_URL}/chat/completions", headers=_headers(), json=body)
    r.raise_for_status()
    data = r.json()
    u = data.get("usage") or {}
    served = r.headers.get("x-model-served", data.get("model", model))
    await _account(case, agent, served, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                   float(u.get("estimated_cost_usd") or r.headers.get("x-estimated-cost-usd") or 0),
                   int((time.perf_counter() - t0) * 1000), "chat")
    return data["choices"][0]["message"]["content"] or ""


async def chat_json(messages: list[dict], **kw) -> dict:
    txt = await chat(messages, json_mode=True, **kw)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"resposta sem JSON: {txt[:200]}")
    return json.loads(m.group(0))


async def jev(state: Any, questions: dict, *, case=None, agent: str = "goxys") -> dict:
    """Decisão estruturada via Jev. Retorna o dict `answers`."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(f"{config.AI_PROXY_BASE_URL}/systemone", headers=_headers(),
                           json={"state": state, "questions": questions})
    r.raise_for_status()
    data = r.json()
    u = data.get("usage") or {}
    await _account(case, agent, data.get("model", "jev"), u.get("input_tokens", 0), u.get("output_tokens", 0),
                   float(u.get("estimated_cost_usd") or 0), int((time.perf_counter() - t0) * 1000), "jev")
    return data["answers"]
