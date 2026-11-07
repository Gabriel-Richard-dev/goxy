"""Adaptador do Reclame Aqui (CONTRACTS §9). Fala só HTTP com a API do Reclame Aqui (hoje a simulada em
/api/reclameaqui; na integração oficial muda `RA_BASE`/autenticação) e com a API do Goxy.

Entrada: polling (20 s) + gatilho `ra.nova` → reclamações pendentes viram casos (POST /api/goxys/casos).
Saída: `message.sent` com canal reclame_aqui → pede aprovação (nível C) → só publica a resposta pública
depois de `approval.decided` aprovado/ajustado → manda a mensagem privada com os detalhes do pedido.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from core.bus import bus

from . import comum

log = logging.getLogger("goxys.channels.reclame_aqui")

RA_BASE = os.getenv("RECLAME_AQUI_API", "/api/reclameaqui")   # caminho na base dos sistemas (ou URL oficial)
POLL_S = 20
ACAO = "publicar_resposta_publica_ra"
TITULO_APROVACAO = "Publicar resposta pública no Reclame Aqui"

_api = comum.api                     # os testes trocam por um cliente em memória
_task: asyncio.Task | None = None
_lock = asyncio.Lock()
VINCULOS: dict[str, str] = {}        # case_id -> reclamacao_id
PENDENTES: dict[str, dict] = {}      # aprovacao_id -> {reclamacao_id, case_id, texto, order_id}
_FEITAS: set[str] = set()

# ------------------------------------------------------------------ sanitização (camada extra)

_PADROES = [
    ("cpf", re.compile(r"(?<!\w)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\w)")),
    ("cartao", re.compile(r"(?<!\w)(?:\d[ .-]?){12,18}\d(?!\w)")),
    ("cpf", re.compile(r"(?<!\w)\d{11}(?!\w)")),
    ("telefone", re.compile(r"(?<!\w)(?:\+?55[\s-]?)?\(?\d{2}\)?[\s-]?9?\d{4}[\s-]?\d{4}(?!\w)")),
]


def sanitizar(texto: str) -> tuple[str, list[str]]:
    """Remove CPF, número de cartão e telefone. Devolve (texto, tipos removidos)."""
    removidos: list[str] = []
    for tipo, rx in _PADROES:
        texto, n = rx.subn("[dado pessoal removido]", texto)
        removidos += [tipo] * n
    return texto, removidos


# ------------------------------------------------------------------ entrada

async def importar_pendentes() -> list[dict]:
    """Cria um caso no Goxy para cada reclamação pendente de importação."""
    criados: list[dict] = []
    async with _lock:
        st, recs = await _api("GET", f"{RA_BASE}/reclamacoes?importacao=pendente")
        if st != 200 or not isinstance(recs, list):
            return criados
        for rec in recs:
            rid = rec["reclamacao_id"]
            mensagem = f"{rec.get('titulo') or ''}\n\n{rec.get('texto') or ''}".strip()
            st, caso = await _api("POST", "/api/goxys/casos", {
                "canal": "reclame_aqui", "mensagem": mensagem, "customer_id": rec.get("customer_id"),
                "order_id": rec.get("order_id"), "conversation_id": rec.get("conversation_id")})
            if st >= 300 or not isinstance(caso, dict) or not caso.get("case_id"):
                log.warning("não criou caso para a reclamação %s: %s %s", rid, st, caso)
                continue
            cid = caso["case_id"]
            VINCULOS[cid] = rid
            await _api("POST", f"{RA_BASE}/reclamacoes/{rid}/vinculo", {"case_id": cid})
            log.info("reclamação %s importada como caso %s", rid, cid)
            criados.append({"reclamacao_id": rid, "case_id": cid})
    return criados


async def _poll() -> None:
    while True:
        try:
            await importar_pendentes()
        except Exception as e:
            log.warning("polling do Reclame Aqui falhou: %s: %s", type(e).__name__, e)
        await asyncio.sleep(POLL_S)


# ------------------------------------------------------------------ saída

async def _reclamacao_do_caso(case_id: str | None) -> str | None:
    if not case_id:
        return None
    if case_id in VINCULOS:
        return VINCULOS[case_id]
    st, recs = await _api("GET", f"{RA_BASE}/reclamacoes?case_id={case_id}")
    if st == 200 and isinstance(recs, list) and recs:
        VINCULOS[case_id] = recs[0]["reclamacao_id"]
        return VINCULOS[case_id]
    return None


# Só a resposta final vira resposta pública (com aprovação). Confirmação, status/SLA, pedido de foto/pedido e
# "um especialista assume" seguem como mensagem privada. Fonte da classificação, nesta ordem:
# 1) `tipo`/`situacao` no próprio `message.sent` (se um dia vier); 2) a mensagem no caso (GET /api/goxys/casos/{id},
# casada por message_id: Voz grava `tipo: "confirmacao"` ou `situacao`); 3) heurística de texto (_RX_STATUS).
FINAIS = {"resolvido", "negado", "final"}
_RX_STATUS = re.compile(r"recebi sua mensagem|já estou cuidando|em análise|passando pra te atualizar|te aviso aqui|"
                        r"me manda (uma foto|o número)|envie uma foto|vai assumir|especialista", re.I)


async def classificar(evt: dict) -> str:
    """'publica' (resposta final) ou 'privada' (confirmação/status)."""
    d, cid = evt.get("data") or {}, evt.get("case_id")
    marca = d.get("tipo") or d.get("situacao")
    if not marca and cid and d.get("message_id"):
        for _ in range(6):                     # a Voz grava a mensagem no caso logo depois do POST
            st, caso = await _api("GET", f"/api/goxys/casos/{cid}")
            if st != 200 or not isinstance(caso, dict):
                break
            m = next((m for m in caso.get("mensagens") or [] if m.get("message_id") == d["message_id"]), None)
            if m:
                marca = m.get("tipo") or m.get("situacao")
                break
            await asyncio.sleep(0.5)
    if marca:
        return "publica" if marca in FINAIS else "privada"
    return "privada" if _RX_STATUS.search(d.get("texto") or "") else "publica"


async def mensagem_privada(evt: dict, rid: str) -> dict | None:
    texto, _ = sanitizar((evt.get("data") or {}).get("texto") or "")
    cid = evt.get("case_id")
    st, pv = await _api("POST", f"{RA_BASE}/reclamacoes/{rid}/mensagem-privada", {"texto": texto, "case_id": cid})
    if st >= 300:
        return None
    await bus.publish("ra.privada", {"reclamacao_id": rid, "resposta_id": pv.get("resposta_id"), "texto": texto,
                                     "motivo": "confirmacao_ou_status"}, case_id=cid, agent="reclame_aqui")
    return pv


async def _rid_com_espera(cid: str | None) -> str | None:
    for _ in range(5):                         # o vínculo pode chegar logo depois do 1º message.sent
        rid = await _reclamacao_do_caso(cid)
        if rid or not cid:
            return rid
        await asyncio.sleep(0.5)
    return None


async def pedir_aprovacao(evt: dict) -> str | None:
    d, cid = evt.get("data") or {}, evt.get("case_id")
    rid = await _rid_com_espera(cid)
    if not rid:
        log.warning("message.sent do caso %s sem reclamação vinculada; nada a publicar", cid)
        return None
    texto, removidos = sanitizar(d.get("texto") or "")
    st, rec = await _api("GET", f"{RA_BASE}/reclamacoes/{rid}")
    order_id = rec.get("order_id") if isinstance(rec, dict) else None
    st, ap = await _api("POST", "/api/aprovacoes", {
        "case_id": cid, "titulo": TITULO_APROVACAO, "resumo": texto, "nivel": "C", "acao_proposta": ACAO,
        "opcoes": ["aprovar", "negar", "ajustar"],
        "evidencias": [{"tipo": "reclame_aqui", "reclamacao_id": rid, "order_id": order_id,
                        "message_id": d.get("message_id"), "dados_removidos": removidos}]})
    if st >= 300 or not isinstance(ap, dict):
        return None
    aid = ap["aprovacao_id"]
    PENDENTES[aid] = {"reclamacao_id": rid, "case_id": cid, "texto": texto, "order_id": order_id}
    return aid


async def rotear_saida(evt: dict) -> dict:
    """message.sent no canal reclame_aqui: final → aprovação; confirmação/status → só mensagem privada."""
    tipo = await classificar(evt)
    if tipo == "publica":
        return {"tipo": tipo, "aprovacao_id": await pedir_aprovacao(evt)}
    rid = await _rid_com_espera(evt.get("case_id"))
    return {"tipo": tipo, "privada": await mensagem_privada(evt, rid) if rid else None}


async def _pendente(aid: str) -> dict | None:
    if aid in PENDENTES:
        return PENDENTES[aid]
    st, ap = await _api("GET", f"/api/aprovacoes/{aid}")       # sobrevive a restart: tudo está na aprovação
    if st != 200 or not isinstance(ap, dict) or ap.get("acao_proposta") != ACAO:
        return None
    ev = next((e for e in ap.get("evidencias") or [] if e.get("reclamacao_id")), {})
    return {"reclamacao_id": ev.get("reclamacao_id"), "case_id": ap.get("case_id"), "texto": ap.get("resumo") or "",
            "order_id": ev.get("order_id")}


async def _texto_privado(case_id: str | None, order_id: str | None) -> str:
    linhas = ["Olá! Seguimos por aqui, no privado, com os detalhes do seu atendimento."]
    if order_id:
        st, p = await _api("GET", f"/api/erp/pedidos/{order_id}")
        if st == 200 and isinstance(p, dict):
            linhas.append(f"Pedido {order_id}: {p.get('descricao_produto') or ''}".rstrip(": "))
            linhas.append(f"Status da entrega: {p.get('status_entrega') or '-'}; transportadora: "
                          f"{p.get('transportadora') or '-'}; rastreio: {p.get('codigo_rastreio') or '-'}; "
                          f"prazo prometido: {p.get('data_prometida') or '-'}.")
        else:
            linhas.append(f"Pedido {order_id}.")
    if case_id:
        linhas.append(f"Protocolo do atendimento: {case_id}.")
    return "\n".join(linhas)


async def ao_decidir(evt: dict) -> dict | None:
    d = evt.get("data") or {}
    aid = d.get("aprovacao_id")
    if not aid or aid in _FEITAS:
        return None
    p = await _pendente(aid)
    if not p or not p.get("reclamacao_id"):
        return None                                   # aprovação de outro fluxo
    _FEITAS.add(aid)
    PENDENTES.pop(aid, None)
    rid, cid = p["reclamacao_id"], p.get("case_id")
    if d.get("decisao") == "negar":
        log.info("resposta pública da reclamação %s negada por %s; nada publicado", rid, d.get("aprovador"))
        await bus.publish("ra.negada", {"reclamacao_id": rid, "aprovacao_id": aid, "aprovador": d.get("aprovador")},
                          case_id=cid, agent="reclame_aqui")
        return {"publicada": False}
    texto = p["texto"]
    nota = ((d.get("ajuste") or {}).get("nota") or "").strip() if d.get("decisao") == "ajustar" else ""
    if nota:
        texto = nota
    texto, removidos = sanitizar(texto)
    st, pub = await _api("POST", f"{RA_BASE}/reclamacoes/{rid}/resposta-publica",
                         {"texto": texto, "aprovacao_id": aid, "case_id": cid})
    if st >= 300:
        log.warning("falhou ao publicar resposta da reclamação %s: %s %s", rid, st, pub)
        return {"publicada": False, "status": st}
    await bus.publish("ra.publicada", {"reclamacao_id": rid, "aprovacao_id": aid, "resposta_id": pub.get("resposta_id"),
                                       "texto": texto, "dados_removidos": removidos,
                                       "url": f"/reclameaqui/reclamacao/{rid}"}, case_id=cid, agent="reclame_aqui")
    priv = await _texto_privado(cid, p.get("order_id"))
    st2, pv = await _api("POST", f"{RA_BASE}/reclamacoes/{rid}/mensagem-privada", {"texto": priv, "case_id": cid})
    if st2 < 300:
        await bus.publish("ra.privada", {"reclamacao_id": rid, "resposta_id": pv.get("resposta_id"), "texto": priv},
                          case_id=cid, agent="reclame_aqui")
    return {"publicada": True, "publica": pub, "privada": pv if st2 < 300 else None}


async def _on_evento(evt: dict) -> None:
    t = evt.get("type")
    if t == "message.sent" and (evt.get("data") or {}).get("canal") == "reclame_aqui":
        await rotear_saida(evt)
    elif t == "approval.decided":
        await ao_decidir(evt)
    elif t == "ra.nova":
        await importar_pendentes()


_registrado = False


async def start() -> None:
    global _task, _registrado
    if not _registrado:
        bus.subscribe_listener(_on_evento)
        _registrado = True
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll())
    log.info("canal reclame_aqui ligado (polling a cada %ss em %s)", POLL_S, RA_BASE)


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None


def _debug() -> dict[str, Any]:
    return {"vinculos": dict(VINCULOS), "pendentes": dict(PENDENTES)}
