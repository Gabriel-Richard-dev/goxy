"""Peças comuns dos canais: cliente HTTP da API e memória das aprovações postadas."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from core import config

log = logging.getLogger("goxys.channels")

# aprovacao_id -> {"pedido": data de approval.requested, "case_id": str}
APROVACOES: dict[str, dict] = {}


def lembrar_aprovacao(evt: dict) -> dict:
    d = evt.get("data") or {}
    aid = str(d.get("aprovacao_id") or "")
    if aid:
        APROVACOES[aid] = {"pedido": d, "case_id": evt.get("case_id")}
    return APROVACOES.get(aid, {"pedido": d, "case_id": evt.get("case_id")})


def base_url() -> str:
    return config.SYSTEMS_BASE_URL.rstrip("/")


async def api(method: str, path: str, json: dict | None = None, timeout: float = 20) -> tuple[int, Any]:
    async with httpx.AsyncClient(base_url=base_url(), timeout=timeout) as cli:
        r = await cli.request(method, path, json=json, headers={"X-Agente": "canal"})
    try:
        body = r.json()
    except ValueError:
        body = r.text
    if r.status_code >= 400:
        log.warning("%s %s -> %s %s", method, path, r.status_code, str(body)[:200])
    return r.status_code, body


async def decidir(aprovacao_id: str, decisao: str, aprovador: str, canal: str, ajuste: dict | None = None) -> tuple[int, Any]:
    body: dict = {"decisao": decisao, "aprovador": aprovador, "canal": canal}
    if ajuste:
        body["ajuste"] = ajuste
    return await api("POST", f"/api/aprovacoes/{aprovacao_id}/decisao", body)


async def pedido_aprovacao(aprovacao_id: str) -> dict:
    """Dados do pedido de aprovação (memória ou GET /api/aprovacoes/{id})."""
    if aprovacao_id in APROVACOES:
        return APROVACOES[aprovacao_id]["pedido"]
    try:
        st, body = await api("GET", f"/api/aprovacoes/{aprovacao_id}")
        if st == 200 and isinstance(body, dict):
            APROVACOES[aprovacao_id] = {"pedido": body, "case_id": body.get("case_id")}
            return body
    except httpx.HTTPError:
        pass
    return {"aprovacao_id": aprovacao_id}


def telegram_chat_do_caso(case_id: str | None) -> str | None:
    if not case_id:
        return None
    try:
        from core.state import store
        c = store.get(case_id)
        return c.telegram_chat_id if c else None
    except Exception:
        return None


def caso_com_pessoa_do_chat(chat_id) -> str | None:
    """Caso aberto deste chat do Telegram que está com uma pessoa (responsavel), se houver."""
    try:
        from core.state import store
        for c in store.all():
            if (str(c.telegram_chat_id) == str(chat_id) and c.responsavel
                    and c.status not in ("resolvido", "rejeitado", "erro")):
                return c.id
    except Exception:
        pass
    return None
