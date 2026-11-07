"""Ferramentas tipadas do Goxy (CONTRACTS §3).

Cada ferramenta chama um sistema simulado por HTTP (base `core.config.SYSTEMS_BASE_URL`), exatamente
como chamaria um sistema real: trocar para produção é mudar o endereço e a autenticação.
Toda chamada vira um `ToolCall` no caso e um evento `tool.called` no barramento.

Escritas recebem `nivel_autonomia` e revalidam a política aqui (defesa em profundidade): o Executor
consulta a política antes, e a ferramenta recusa com `PolicyViolation` se o nível não permitir.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

import httpx
from bs4 import BeautifulSoup

from agents.base import agente_atual, agora_ambiente, dias_uteis_entre, narrar, parse_data
from core import config
from core.bus import bus
from core.state import Anexo, Case, ToolCall, store

log = logging.getLogger("goxys.tools")

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
PORTAL_USUARIO = "goxys"
PORTAL_SENHA = "hackathon"


class PolicyViolation(Exception):
    """Escrita pedida sem nível de autonomia que a permita."""


class ToolError(Exception):
    def __init__(self, ferramenta: str, status: int | None, corpo: Any):
        self.ferramenta, self.status, self.corpo = ferramenta, status, corpo
        msg = corpo.get("mensagem") or corpo.get("erro") if isinstance(corpo, dict) else str(corpo)[:200]
        super().__init__(f"{ferramenta} → HTTP {status}: {msg}")


# ---------------------------------------------------------------- núcleo

def _resumo(v: Any, limite: int = 220) -> str:
    if isinstance(v, dict):
        chaves = ["erro", "chamado_id", "protocolo", "solicitacao_id", "pedido_reposicao_id", "reembolso_id",
                  "aprovacao_id", "message_id", "status", "valor", "transportadora", "order_id", "nome",
                  "avaria_detectada", "confianca", "atrasado", "dias_atraso", "fonte", "idempotente"]
        partes = [f"{k}={v[k]}" for k in chaves if k in v and v[k] not in (None, "", [])]
        if "reuso" in v:
            partes.append(f"reuso={len(v.get('reuso') or [])}")
        if "eventos" in v:
            partes.append(f"eventos={len(v.get('eventos') or [])}")
        s = ", ".join(partes) or ", ".join(list(v.keys())[:8])
    elif isinstance(v, list):
        s = f"{len(v)} itens"
    else:
        s = str(v)
    return s[:limite]


async def _registrar(case: Case, ferramenta: str, tipo: str, parametros: dict, status: int | None,
                     resultado: Any, ms: int, nivel: str | None = None) -> ToolCall:
    tc = ToolCall(acao_id=store.new_acao_id(), agent=agente_atual.get(), ferramenta=ferramenta, tipo=tipo,
                  parametros=parametros, resultado=resultado, status_http=status, nivel_autonomia=nivel,
                  duracao_ms=ms)
    case.acoes.append(tc)
    await bus.publish("tool.called", {"ferramenta": ferramenta, "tipo": tipo, "parametros": parametros,
                                      "status_http": status, "resultado_resumo": _resumo(resultado),
                                      "duracao_ms": ms, "acao_id": tc.acao_id, "nivel_autonomia": nivel},
                      case_id=case.id, agent=tc.agent)
    await store.save(case)
    return tc


async def _http(case: Case, ferramenta: str, tipo: str, method: str, path: str, *, json: dict | None = None,
                params: dict | None = None, idem: str | None = None, nivel: str | None = None,
                aceitar: tuple[int, ...] = ()) -> tuple[int, Any]:
    """Chama o sistema simulado, registra e devolve (status, corpo). Levanta ToolError fora de 2xx/aceitar."""
    headers = {"X-Agente": agente_atual.get(), "X-Caso": case.id}
    if idem:
        headers["Idempotency-Key"] = idem
    parametros = {k: v for k, v in {**(params or {}), **(json or {})}.items()}
    t0 = time.perf_counter()
    status: int | None = None
    try:
        async with httpx.AsyncClient(base_url=config.SYSTEMS_BASE_URL, timeout=TIMEOUT) as cli:
            r = await cli.request(method, path, json=json, params=params, headers=headers)
        status = r.status_code
        try:
            corpo = r.json()
        except ValueError:
            corpo = {"erro": "resposta_nao_json", "mensagem": r.text[:300]}
    except httpx.HTTPError as e:
        corpo = {"erro": "sistema_indisponivel", "mensagem": f"{type(e).__name__}: {e}"}
    ms = int((time.perf_counter() - t0) * 1000)
    await _registrar(case, ferramenta, tipo, parametros, status, corpo, ms, nivel)
    if status is None or (status >= 400 and status not in aceitar):
        raise ToolError(ferramenta, status, corpo)
    return status, corpo


def idem_key(case: Case, acao: str) -> str:
    """Idempotency-Key = case_id + ação. O id do caso reinicia com o processo (GX{HHMM}{seq}), então
    o instante de criação entra na chave para dois casos diferentes nunca colidirem."""
    carimbo = re.sub(r"\D", "", case.criado_em or "")
    return f"{case.id}-{carimbo}:{acao}"


def _guard(case: Case, ferramenta: str, nivel: str | None, aprovacao_id: str | None = None) -> None:
    """Revalida a política na própria ferramenta (o Executor já consultou antes)."""
    if case.responsavel:
        raise PolicyViolation(f"{ferramenta}: caso com {case.responsavel.get('nome')}; ações do Goxy congeladas")
    if case.decisao.get("congelado"):
        raise PolicyViolation(f"{ferramenta}: ações congeladas neste caso (humano assumiu)")
    if nivel not in ("A", "B", "C"):
        raise PolicyViolation(f"{ferramenta}: nível de autonomia '{nivel}' não permite escrita")
    if nivel == "C":
        ap = case.aprovacao or {}
        if not aprovacao_id or ap.get("aprovacao_id") != aprovacao_id or ap.get("decisao") not in ("aprovar", "ajustar"):
            raise PolicyViolation(f"{ferramenta}: nível C exige aprovação humana registrada")


# ---------------------------------------------------------------- leitura

async def get_order(case: Case, order_id: str) -> dict:
    _, corpo = await _http(case, "get_order", "leitura", "GET", f"/api/erp/pedidos/{order_id}")
    return corpo


async def get_customer(case: Case, customer_id: str) -> dict:
    _, corpo = await _http(case, "get_customer", "leitura", "GET", f"/api/erp/clientes/{customer_id}")
    return corpo


async def get_customer_history(case: Case, customer_id: str) -> dict:
    _, corpo = await _http(case, "get_customer_history", "leitura", "GET",
                           f"/api/erp/clientes/{customer_id}/historico")
    return corpo


async def get_attachments(case: Case, *, conversation_id: str | None = None, order_id: str | None = None,
                          hash: str | None = None) -> list[dict]:
    params = {k: v for k, v in {"conversation_id": conversation_id, "order_id": order_id, "hash": hash}.items() if v}
    _, corpo = await _http(case, "get_attachments", "leitura", "GET", "/api/erp/anexos", params=params)
    if isinstance(corpo, dict):
        corpo = corpo.get("anexos") or corpo.get("itens") or []
    return corpo


def _calc_atraso(t: dict, data_prometida: str | None) -> dict:
    prom = parse_data(data_prometida or t.get("data_prometida"))
    status = (t.get("status") or "").lower()
    entregue = "entreg" in status
    if prom and not entregue and "dias_atraso" not in t:
        hoje = agora_ambiente().date()
        dias = (hoje - prom.date()).days
        t["atrasado"] = dias > 0
        t["dias_atraso"] = max(dias, 0)
        t["dias_uteis_atraso"] = max(dias_uteis_entre(prom.date(), hoje), 0) if dias > 0 else 0
        t["data_prometida"] = t.get("data_prometida") or data_prometida
    return t


async def get_tracking(case: Case, order_id: str, *, data_prometida: str | None = None) -> dict:
    """Rastreio pela API da transportadora. Sem API (HTTP 501) → agente de navegador no portal."""
    status, corpo = await _http(case, "get_tracking", "leitura", "GET", f"/api/erp/rastreio/{order_id}",
                                aceitar=(501,))
    if status == 501:
        transp = corpo.get("transportadora") or "transportadora"
        await narrar(case, titulo=f"API da {transp} indisponível → entrando no portal",
                     detalhe=f"GET /api/erp/rastreio/{order_id} respondeu 501 ({corpo.get('mensagem', 'sem API')}). "
                             "Abrindo o portal web com login e lendo a página.",
                     evidencia={"tipo": "fallback", "titulo": "API respondeu 501: sem API de rastreio",
                                "detalhe": corpo.get("mensagem"), "ref": f"/api/erp/rastreio/{order_id}"})
        t = await portal_tracking(case, order_id, data_prometida=data_prometida,
                                  portal=corpo.get("portal") or "/portal")
        t["motivo_fallback"] = corpo.get("mensagem") or "API indisponível (501)"
        if not t.get("transportadora") and corpo.get("transportadora"):
            t["transportadora"] = corpo["transportadora"]
        return t
    corpo.setdefault("fonte", "api")
    return _calc_atraso(corpo, data_prometida)


async def portal_tracking(case: Case, order_id: str, *, data_prometida: str | None = None,
                          portal: str = "/portal") -> dict:
    """Agente de navegador: faz login no portal da transportadora (cookie de sessão) e extrai os eventos do HTML."""
    t0 = time.perf_counter()
    passos: list[str] = []
    parametros = {"order_id": order_id, "portal": portal, "usuario": PORTAL_USUARIO}
    status: int | None = None
    try:
        async with httpx.AsyncClient(base_url=config.SYSTEMS_BASE_URL, timeout=TIMEOUT,
                                     follow_redirects=True, headers={"X-Agente": agente_atual.get()}) as nav:
            r = await nav.get(portal)
            passos.append(f"GET {portal} → {r.status_code}")
            r = await nav.post(f"{portal}/login", data={"usuario": PORTAL_USUARIO, "senha": PORTAL_SENHA})
            passos.append(f"POST {portal}/login → {r.status_code} (cookie de sessão: {'sim' if nav.cookies else 'não'})")
            await narrar(case, titulo="entrei no portal da transportadora, lendo o rastreio")
            r = await nav.get(f"{portal}/rastreio", params={"pedido": order_id})
            status = r.status_code
            passos.append(f"GET {portal}/rastreio?pedido={order_id} → {r.status_code}")
            html = r.text
        if status >= 400:
            raise ToolError("portal_tracking", status, {"erro": "portal_erro", "mensagem": html[:200]})
        soup = BeautifulSoup(html, "html.parser")
        eventos = []
        tabela = soup.find(id="eventos")
        for tr in (tabela.find_all("tr") if tabela else []):
            if not tr.get("data-status"):
                continue
            eventos.append({"data_hora": tr.get("data-hora"), "status": tr.get("data-status"),
                            "local": tr.get("data-local")})
        if tabela is None:
            raise ToolError("portal_tracking", status, {"erro": "layout_inesperado",
                                                        "mensagem": "tabela #eventos não encontrada (login falhou?)"})
        eventos.sort(key=lambda e: e.get("data_hora") or "")
        prev = soup.find(id="previsao")
        transp = soup.find(id="transportadora")
        codigo = soup.find(id="codigo")
        t = {
            "fonte": "portal",
            "transportadora": transp.get_text(strip=True) if transp else None,
            "codigo_rastreio": codigo.get_text(strip=True) if codigo else None,
            "status": eventos[-1]["status"] if eventos else None,
            "previsao_entrega": prev.get_text(strip=True) if prev else None,
            "data_prometida": data_prometida,
            "eventos": eventos,
            "navegacao": passos,
        }
        t = _calc_atraso(t, data_prometida)
        ms = int((time.perf_counter() - t0) * 1000)
        await _registrar(case, "portal_tracking", "leitura", parametros, status, t, ms)
        return t
    except (httpx.HTTPError, ToolError) as e:
        ms = int((time.perf_counter() - t0) * 1000)
        corpo = {"erro": "portal_falhou", "mensagem": str(e), "navegacao": passos}
        await _registrar(case, "portal_tracking", "leitura", parametros, status, corpo, ms)
        raise ToolError("portal_tracking", status, corpo) from e


async def analyze_damage_photo(case: Case, anexo: Anexo | dict) -> dict:
    a = anexo.model_dump() if isinstance(anexo, Anexo) else dict(anexo)
    _, corpo = await _http(case, "analyze_damage_photo", "leitura", "POST", "/api/erp/pericia-foto",
                           json={"order_id": case.order_id, "customer_id": case.customer_id, "anexo": a})
    return corpo


# ---------------------------------------------------------------- escrita

async def open_carrier_ticket(case: Case, order_id: str, motivo: str, *, nivel_autonomia: str | None,
                              aprovacao_id: str | None = None) -> dict:
    _guard(case, "open_carrier_ticket", nivel_autonomia, aprovacao_id)
    _, corpo = await _http(case, "open_carrier_ticket", "escrita", "POST", "/api/logistica/chamado-transportadora",
                           json={"order_id": order_id, "motivo": motivo}, idem=idem_key(case, "chamado_transportadora"),
                           nivel=nivel_autonomia)
    return corpo


async def create_exchange(case: Case, order_id: str, sku: str | None,
                          tipo: Literal["reenvio", "troca", "nova_producao"], motivo: str, *,
                          nivel_autonomia: str | None, aprovacao_id: str | None = None, quantidade: int = 1) -> dict:
    _guard(case, "create_exchange", nivel_autonomia, aprovacao_id)
    _, corpo = await _http(case, "create_exchange", "escrita", "POST", "/api/logistica/trocas",
                           json={"order_id": order_id, "sku": sku, "tipo": tipo, "motivo": motivo,
                                 "quantidade": quantidade, "aprovacao_id": aprovacao_id},
                           idem=idem_key(case, tipo), nivel=nivel_autonomia)
    return corpo


async def issue_refund(case: Case, order_id: str, valor: float, motivo: str, aprovacao_id: str | None = None, *,
                       nivel_autonomia: str | None) -> dict:
    """Escrita crítica. Idempotency-Key = case_id + ação: nunca paga duas vezes o mesmo caso."""
    _guard(case, "issue_refund", nivel_autonomia, aprovacao_id)
    body = {"order_id": order_id, "valor": round(float(valor), 2), "motivo": motivo}
    if aprovacao_id:
        body["aprovacao_id"] = aprovacao_id
    _, corpo = await _http(case, "issue_refund", "escrita_critica", "POST", "/api/financeiro/reembolsos",
                           json=body, idem=idem_key(case, "reembolso"), nivel=nivel_autonomia)
    return corpo


async def cancel_exchange(case: Case, solicitacao_id: str, motivo: str) -> dict:
    """Desfazer (nível B): cancela a reposição. Pedido por uma pessoa; idempotente no sistema."""
    _, corpo = await _http(case, "cancel_exchange", "escrita", "POST", f"/api/logistica/trocas/{solicitacao_id}/cancelar",
                           json={"motivo": motivo, "case_id": case.id})
    return corpo


async def reverse_refund(case: Case, reembolso_id: str, motivo: str) -> dict:
    """Desfazer (nível B): estorna o reembolso. Pedido por uma pessoa; idempotente no sistema."""
    _, corpo = await _http(case, "reverse_refund", "escrita_critica", "POST",
                           f"/api/financeiro/reembolsos/{reembolso_id}/estornar", json={"motivo": motivo, "case_id": case.id})
    return corpo


async def send_message(case: Case, texto: str) -> dict:
    """Responde ao cliente no canal de origem (o sistema de mensageria publica `message.sent`)."""
    body = {"canal": case.canal, "customer_id": case.customer_id, "conversation_id": case.conversation_id,
            "case_id": case.id, "texto": texto}
    if case.telegram_chat_id:
        body["telegram_chat_id"] = case.telegram_chat_id
    _, corpo = await _http(case, "send_message", "escrita", "POST", "/api/mensagens", json=body)
    return corpo


async def request_approval(case: Case, *, titulo: str, resumo: str, nivel: str, valor: float | None,
                           acao_proposta: str, evidencias: list[dict], prazo_s: int | None = None,
                           opcoes: list[str] | None = None, recomendacao: dict | None = None,
                           solicitante: str | None = None) -> dict:
    body = {"case_id": case.id, "titulo": titulo, "resumo": resumo, "nivel": nivel, "valor": valor,
            "acao_proposta": acao_proposta, "opcoes": opcoes or ["aprovar", "negar", "ajustar"],
            "evidencias": evidencias, "prazo_s": prazo_s or config.APPROVAL_SLA_S}
    if recomendacao:
        body["recomendacao"] = recomendacao
    if solicitante:
        body["solicitante"] = solicitante
    _, corpo = await _http(case, "request_approval", "humano", "POST", "/api/aprovacoes", json=body)
    return corpo


async def decide_approval(case: Case, aprovacao_id: str, decisao: str, aprovador: str, canal: str = "painel",
                          ajuste: dict | None = None) -> dict:
    """Registra no sistema de aprovações uma decisão humana tomada fora dos botões (ex.: nota ao devolver o caso)."""
    body = {"decisao": decisao, "aprovador": aprovador, "canal": canal}
    if ajuste:
        body["ajuste"] = ajuste
    _, corpo = await _http(case, "decide_approval", "humano", "POST", f"/api/aprovacoes/{aprovacao_id}/decisao",
                           json=body)
    return corpo


async def open_internal_ticket(case: Case, assunto: str | None = None) -> dict:
    body = {"conversation_id": case.conversation_id, "customer_id": case.customer_id, "order_id": case.order_id,
            "canal": case.canal, "assunto": assunto or (case.triagem.get("intencao") or "atendimento"),
            "case_id": case.id}
    _, corpo = await _http(case, "open_internal_ticket", "escrita", "POST", "/api/chamados", json=body)
    return corpo


async def update_internal_ticket(case: Case, chamado_id: str, status: str, nota: str) -> dict:
    _, corpo = await _http(case, "update_internal_ticket", "escrita", "PATCH", f"/api/chamados/{chamado_id}",
                           json={"status": status, "nota": nota})
    return corpo


# ---------------------------------------------------------------- utilidades

PII_RE = [
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF omitido]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[cartão omitido]"),
    (re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b"), "[telefone omitido]"),
]


def mascarar_pii(texto: str) -> str:
    """Nunca repetir CPF, cartão ou telefone (política). Aplica-se a tudo que sai para o cliente ou para o LLM."""
    for rx, sub in PII_RE:
        texto = rx.sub(sub, texto or "")
    return texto
