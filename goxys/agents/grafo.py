"""Maestro como grafo do LangGraph (`StateGraph`), com checkpoint durável em SQLite.

    START ─┬─(entrada=inicio)──▶ inicio ▶ porteiro ─┬─▶ rastreador ───┐
           │                                         ├─▶ detetive_prep ┴▶ detetive ─┐
           │                                         ├─▶ analista ──────────────────┴▶ politica
           │                                         └─(sem pedido)▶ voz
           └─(entrada=politica: devolução humana)──────────────────────────────────▶ politica
    politica ─┬─ A/B ▶ executor ▶ voz
              ├─ C/D ▶ revisor ─┬─ C ▶ pedir_aprovacao ▶ aprovacao (interrupt) ─┬─ aprovar/ajustar ▶ executor
              │                 │                                              └─ negar ▶ negar ▶ voz
              │                 └─ D ▶ humano ▶ voz
              └─ pedir foto / aprovado por humano na devolução ▶ voz | executor

- Estado do grafo: `{case_id, entrada, sinal_humano}`. O `Case` vive no `CaseStore` e cada passo salva o snapshot
  em `goxys_cases`, então o caso é recarregável depois de um reinício.
- Checkpointer `AsyncSqliteSaver` em `data/checkpoints.db`, `thread_id = case_id` (devolução humana abre `case_id~n`).
- Aprovação = `interrupt()`; `approval.decided` → `Command(resume=decisao)`. Sobrevive a reinício do servidor.
- Nenhuma escrita sem a política; uma pessoa com o caso (`case.responsavel`) congela os nós do Goxy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, TypedDict

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import tools
from core import config, llm
from core.bus import bus
from core.state import Case, TimelineStep, now, store

from . import detetive, equipe, executor, mensageiro, politica, porteiro, rastreador, revisor, voz
from .base import brl, mascote, passo

log = logging.getLogger("goxys.grafo")

CHECKPOINTS = Path(os.getenv("GOXYS_CHECKPOINTS", str(config.DATA_DIR / "checkpoints.db")))


class Estado(TypedDict, total=False):
    case_id: str
    entrada: str                      # "inicio" | "politica"
    sinal_humano: Optional[dict]      # nota da pessoa que devolveu o caso, interpretada


PLANO_BASE = [
    {"id": "porteiro", "agent": "porteiro", "titulo": "classificar o contato", "depende_de": []},
    {"id": "rastreador", "agent": "rastreador", "titulo": "reunir pedido, cliente e rastreio", "depende_de": ["porteiro"],
     "paralelo_com": ["detetive", "analista"]},
    {"id": "detetive", "agent": "detetive", "titulo": "periciar a evidência", "depende_de": ["porteiro"],
     "paralelo_com": ["rastreador", "analista"], "espera_dados_de": ["rastreador"]},
    {"id": "politica", "agent": "maestro", "titulo": "decidir o nível de autonomia", "depende_de": ["rastreador", "detetive"]},
    {"id": "acao", "agent": "executor|mensageiro|humano", "titulo": "agir, pedir aprovação ou passar a uma pessoa",
     "depende_de": ["politica"]},
    {"id": "voz", "agent": "voz", "titulo": "responder ao cliente", "depende_de": ["acao"]},
]

_graph = None
_conn: aiosqlite.Connection | None = None
_sla: dict[str, asyncio.Task] = {}
_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()


def _lock(case_id: str) -> asyncio.Lock:
    return _locks.setdefault(case_id, asyncio.Lock())


def _case(state: Estado) -> Case:
    c = store.get(state["case_id"])
    if c is None:
        raise KeyError(f"caso {state['case_id']} não está no store")
    return c


def _marcar(case: Case, no: str, status: str, **extra) -> None:
    for n in case.plano:
        if n["id"] == no:
            n["status"] = status
            n.update(extra)


async def _fechar(case: Case, status: str, mascot: str, resumo: str) -> None:
    await store.set_status(case, status, mascot)  # type: ignore[arg-type]
    await bus.publish("case.closed", {"status": status, "resumo": resumo}, case_id=case.id, agent="maestro")


def _com_humano(case: Case) -> bool:
    return bool(case.responsavel)


# ------------------------------------------------------------------ nós

async def no_inicio(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    case.status = "em_andamento"
    case.plano = [dict(n, status="pendente") for n in PLANO_BASE]
    if not case.timeline:
        case.timeline = [TimelineStep(agent=a) for a in ("maestro", "porteiro", "rastreador", "detetive")]
    await mascote(case, "investigando")
    await voz.confirmar(case)
    async with passo(case, "maestro", "montando o plano do caso") as s:
        s.titulo = "plano: porteiro → (rastreador ‖ detetive ‖ analista) → política → ação → voz"
        s.detalhe = ("Grafo do LangGraph com checkpoint em SQLite: Rastreador, perícia e Analista em paralelo; "
                     "nenhuma escrita sem a política de autonomia.")
        s.evidencias.append({"tipo": "plano", "titulo": "grafo do caso", "plano": case.plano,
                             "ref": "/api/goxys/grafo"})
    return {}


async def no_porteiro(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    _marcar(case, "porteiro", "rodando")
    await porteiro.run(case)
    _marcar(case, "porteiro", "ok")
    _marcar(case, "rastreador", "rodando")
    _marcar(case, "detetive", "rodando")
    return {}


async def _seguro(case: Case, no: str, coro) -> None:
    """Ramo paralelo: uma falha marca o nó e não derruba o caso (o passo já registra o erro)."""
    try:
        await coro
        _marcar(case, no, "ok")
    except Exception:
        log.exception("nó %s falhou no caso %s", no, case.id)
        _marcar(case, no, "falhou")


async def no_rastreador(state: Estado) -> dict:
    case = _case(state)
    if not _com_humano(case):
        await _seguro(case, "rastreador", rastreador.run(case))
    return {}


async def no_detetive_prep(state: Estado) -> dict:
    case = _case(state)
    if not _com_humano(case):
        await _seguro(case, "detetive", detetive.preparar(case))
    return {}


async def no_analista(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    try:
        from . import analista
        await _seguro(case, "analista", analista.run(case))
    except ImportError:
        pass
    return {}


async def no_detetive(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    await _seguro(case, "detetive", detetive.concluir(case))
    if (case.veracidade or {}).get("faixa") == "vermelha":
        await mascote(case, "fraude")
    return {}


async def _justificar(case: Case) -> str:
    dec = case.decisao
    base = (f"Nível {dec['nivel']} ({dec.get('comportamento', '')}). {dec['motivo']}. "
            f"Ação: {dec['acao']}" + (f" de {brl(dec['valor'])}" if dec.get("valor") else "") +
            f". {dec['sinais']['custo_erro']['leitura']}. Política {dec['politica_versao']}.")
    if not llm.enabled():
        return base
    try:
        txt = await llm.chat([
            {"role": "system", "content": "Você é o Maestro do Goxy. Explique para o supervisor, em até 2 frases, "
             "por que esta decisão de autonomia foi tomada, usando só os fatos dados. Não mude nível, ação nem valores."},
            {"role": "user", "content": base},
        ], case=case, agent="maestro", max_tokens=2000, temperature=0.2)
        return (txt or "").strip() or base
    except Exception:
        return base


async def _polir_porque(case: Case, frase: str) -> str:
    """A IA só pode polir a frase determinística: ≤ 140, mesmos números, sem jargão."""
    if not llm.enabled():
        return frase
    try:
        txt = await llm.chat([
            {"role": "system", "content": "Reescreva em UMA frase curta (até 140 caracteres), humana e sem jargão, "
             "para um supervisor de atendimento. Mantenha todos os fatos e números. Responda só com a frase."},
            {"role": "user", "content": frase},
        ], case=case, agent="maestro", max_tokens=2000, temperature=0.2)
        txt = (txt or "").strip().strip('"')
        nums = re.findall(r"\d+(?:[.,]\d+)*", frase)
        if txt and len(txt) <= 140 and all(n in txt for n in nums) and "\n" not in txt:
            return txt
    except Exception:
        pass
    return frase


def _aplicar_sinal_humano(case: Case, dec: dict, sinal: dict | None) -> None:
    """Devolução com nota: 'pode fazer o reenvio' vira aprovação humana registrada; 'não procede' vira negativa."""
    ap = case.aprovacao or {}
    tipo = (sinal or {}).get("tipo")
    if not tipo and ap.get("decisao") in ("aprovar", "ajustar"):
        tipo = "aprovar"
    elif not tipo and ap.get("decisao") == "negar":
        tipo = "negar"
    if not tipo:
        return
    dec["sinal_humano"] = {**(sinal or {}), "tipo": tipo}
    if tipo == "negar":
        dec["negado_por_humano"] = True
        dec["motivo"] = f"Negado por {(sinal or {}).get('nome') or ap.get('aprovador') or 'humano'}. " + dec["motivo"]
        return
    nova = (sinal or {}).get("acao") or ((ap.get("ajuste") or {}).get("acao"))
    if not nova and dec["acao"] in ("humano", "negar"):
        nova = dec.get("acao_sugerida") or politica.propor_acao(case)["acao"]
    if nova and nova != dec["acao"]:
        dec["acao_original"] = dec["acao"]
        dec["acao"] = nova
        pedido = (case.dossie or {}).get("pedido") or {}
        if not dec.get("valor") and nova in ("reenvio", "troca", "nova_producao", "reembolso"):
            dec["valor"] = float(pedido.get("valor") or 0)
        dec["tipo_troca"] = nova if nova in ("reenvio", "troca", "nova_producao") else None
    valor = (sinal or {}).get("valor") or (ap.get("ajuste") or {}).get("valor")
    if valor is not None:
        dec["valor_original"], dec["valor"] = dec.get("valor"), float(valor)
    dec["nivel_original"] = dec["nivel"]
    dec["nivel"] = "C"            # escrita com aprovação humana registrada
    dec["congelado"] = False
    dec["aprovado_por_humano"] = True
    dec["motivo"] = f"Aprovado por {(sinal or {}).get('nome') or ap.get('aprovador') or 'humano'}. " + dec["motivo"]


async def _registrar_aprovacao_humana(case: Case, sinal: dict) -> tuple[bool, str]:
    """Sem aprovação no sistema? Cria e decide em nome de quem devolveu (trilha de auditoria + trava do financeiro).
    O sistema de aprovações aplica segregação e alçada: se a pessoa não pode aprovar, a aprovação fica pendente."""
    ap = case.aprovacao or {}
    if ap.get("decisao") in ("aprovar", "ajustar"):
        return True, ""
    dec = case.decisao
    if not ap.get("aprovacao_id"):
        rec = mensageiro.recomendacao(case)
        r = await tools.request_approval(case, titulo=mensageiro.titulo_curto(case),
                                         resumo=f"Pedido na devolução do caso por {sinal.get('nome')}: {sinal.get('nota') or ''}",
                                         nivel="C", valor=dec.get("valor"), acao_proposta=dec.get("acao"),
                                         evidencias=[], prazo_s=config.APPROVAL_SLA_S, recomendacao=rec)
        ap = {"aprovacao_id": r.get("aprovacao_id"), "status": "pendente", "expira_em": r.get("expira_em"),
              "titulo": mensageiro.titulo_curto(case), "valor": dec.get("valor"), "recomendacao": rec,
              "solicitante": r.get("solicitante"), "solicitada_em": r.get("criado_em")}
        case.aprovacao = ap
    quem = sinal.get("nome") or "humano"
    try:
        r = await tools.decide_approval(case, ap["aprovacao_id"], "aprovar", quem, "painel")
    except tools.ToolError as e:
        if e.status == 403:                       # segregação/alçada: precisa de outra pessoa
            msg = (e.corpo or {}).get("mensagem") if isinstance(e.corpo, dict) else str(e)
            ap["bloqueio"] = msg
            return False, msg or "precisa de outra pessoa"
        if e.status != 409:
            raise
        r = {}
    ap.update({"status": "aprovada", "decisao": "aprovar", "aprovador": quem, "canal": "painel", "ajuste": {},
               "via": "devolucao", "nota": sinal.get("nota"), "decidida_em": now(),
               "concordou": (r or {}).get("concordou")})
    return True, ""


async def no_politica(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    async with passo(case, "maestro", "consultando a política de autonomia", novo=True) as s:
        dec = politica.decide(case)
        anterior = case.decisao or {}
        for k in ("cosmetovigilancia", "chamado_humano"):      # registros já feitos continuam valendo
            if anterior.get(k):
                dec[k] = anterior[k]
        case.decisao = dec
        _aplicar_sinal_humano(case, dec, state.get("sinal_humano"))
        _marcar(case, "politica", "ok", nivel=dec["nivel"])
        await bus.publish("policy.decided", {"nivel": dec["nivel"], "acao": dec["acao"], "valor": dec["valor"],
                                             "motivo": dec["motivo"], "sinais": dec["sinais"],
                                             "porque": dec.get("porque"),
                                             "politica_versao": dec["politica_versao"]},
                          case_id=case.id, agent="maestro")
        dec["justificativa"], dec["porque"] = await asyncio.gather(_justificar(case),
                                                                   _polir_porque(case, dec["porque"]))
        dec["evidencias"] = [e["titulo"] for e in (case.veracidade or {}).get("evidencias", [])] + \
                            (case.triagem or {}).get("gatilhos", [])
        dec["decidido_em"] = now()
        case.sugestao_responsavel = equipe.sugerir(case, dec["nivel"])
        s.titulo = f"nível {dec['nivel']}: {dec['motivo'].split(':')[0][:90]}"
        s.detalhe = f"{dec['porque']}\n{dec['justificativa']}"
        s.evidencias.append({"tipo": "politica", "titulo": f"política {dec['politica_versao']} · nível {dec['nivel']}",
                             "detalhe": dec["motivo"], "sinais": dec["sinais"]})
        s.evidencias.append({"tipo": "custo_erro", "titulo": "custo de erro assimétrico",
                             "detalhe": dec["sinais"]["custo_erro"]["leitura"]})
        if case.sugestao_responsavel:
            sr = case.sugestao_responsavel
            s.evidencias.append({"tipo": "sugestao", "titulo": f"sugestão: passar para {sr['nome']} ({sr['cargo']})",
                                 "detalhe": sr["motivo"]})
        if dec.get("aprovado_por_humano"):
            ok, msg = await _registrar_aprovacao_humana(case, dec["sinal_humano"])
            if not ok:                            # volta a ser uma aprovação pendente normal (outra pessoa decide)
                dec["aprovado_por_humano"] = False
                dec["motivo"] = f"{msg} " + dec["motivo"]
                s.evidencias.append({"tipo": "segregacao", "titulo": "a aprovação precisa de outra pessoa", "detalhe": msg})
        if dec["nivel"] in ("C", "D") and not dec.get("aprovado_por_humano"):
            s.status = "alerta"
    return {}


def rota_porteiro(state: Estado):
    case = _case(state)
    if _com_humano(case):
        return END
    if not case.order_id and not case.customer_id:
        return "voz"
    return ["rastreador", "detetive_prep", "analista"]


def rota_inicio(state: Estado) -> str:
    return "politica" if state.get("entrada") == "politica" else "inicio"


def rota_politica(state: Estado) -> str:
    case = _case(state)
    dec = case.decisao
    if _com_humano(case):
        return END
    if dec.get("negado_por_humano"):
        return "negar"
    if dec.get("aprovado_por_humano"):
        return "executor"
    if dec.get("acao") == "solicitar_foto":
        return "voz"
    if dec["nivel"] in ("C", "D"):
        return "revisor"
    return "executor"


async def no_revisor(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    dec = case.decisao
    async with passo(case, "maestro", "revisor confere a decisão (maker-checker)", novo=True) as s:
        rv = revisor.revisar(case)
        dec["revisor"] = rv
        if rv.get("rebaixar_para"):
            dec["nivel"] = rv["rebaixar_para"]
            dec["acao"] = politica.propor_acao(case)["acao"]
            dec["motivo"] += " · revisor rebaixou para aprovação humana"
            case.sugestao_responsavel = equipe.sugerir(case, dec["nivel"])
        s.titulo = f"revisor: {rv['parecer']}"
        s.detalhe = "\n".join(rv["ressalvas"]) or "Sem ressalvas."
        s.evidencias = [{"tipo": "checagem", "titulo": c["item"], "ok": c["ok"]} for c in rv["checagens"]]
        if rv["ressalvas"] or not rv["aprovado"]:
            s.status = "alerta"
    return {}


def rota_revisor(state: Estado) -> str:
    case = _case(state)
    if _com_humano(case):
        return END
    return "humano" if case.decisao["nivel"] == "D" else "pedir_aprovacao"


async def _registrar_cosmetovigilancia(case: Case) -> None:
    """Reação adversa: o registro (produto, lote, descrição, fotos) sai já, dentro das 24 h. Trocar não substitui."""
    if (case.decisao.get("cosmetovigilancia") or {}).get("chamado_id"):
        return
    pedido = (case.dossie or {}).get("pedido") or {}
    lote = pedido.get("lote_produto") or next((i.get("lote") for i in pedido.get("itens") or [] if i.get("lote")), None)
    reg = {"sku": pedido.get("sku"), "produto": pedido.get("descricao_produto"), "lote": lote,
           "descricao": (case.mensagem or "")[:500], "fotos": [a.anexo_id or a.url for a in case.anexos],
           "prazo_ate": (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")}
    async with passo(case, "maestro", "registrando cosmetovigilância (prazo 24 h)", novo=True) as s:
        try:
            ch = await tools.open_internal_ticket(
                case, f"Cosmetovigilância (24 h): {reg['produto'] or reg['sku']} · lote {lote or '?'}")
            reg["chamado_id"] = ch.get("chamado_id")
            s.titulo = f"cosmetovigilância registrada: {ch.get('chamado_id')} · lote {lote or 'sem lote'}"
        except Exception as e:
            s.status, s.titulo, s.detalhe = "falhou", "não consegui registrar a cosmetovigilância", str(e)
        s.evidencias.append({"tipo": "cosmetovigilancia", "titulo": "registro obrigatório", "detalhe": str(reg)})
        case.decisao["cosmetovigilancia"] = reg


async def no_pedir_aprovacao(state: Estado, config: RunnableConfig) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    dec = case.decisao
    if dec.get("acao") == "cosmetovigilancia":
        await _registrar_cosmetovigilancia(case)
    _marcar(case, "acao", "rodando", ramo="mensageiro")
    ap = case.aprovacao or {}
    if ap.get("aprovacao_id") and not ap.get("decisao"):   # devolução com aprovação ainda pendente: reaproveita
        async with passo(case, "mensageiro", "aprovação continua pendente", novo=True, atraso=False) as s:
            s.status, s.titulo = "alerta", f"aprovação {ap['aprovacao_id']} continua pendente"
    else:
        case.aprovacao = {}
        await mensageiro.run(case)
        await voz.run(case, "em_analise")
    case.aprovacao["thread_id"] = config["configurable"]["thread_id"]
    await store.set_status(case, "aguardando_aprovacao", "esperando")
    _iniciar_sla(case)
    return {}


async def no_aprovacao(state: Estado) -> dict:
    """Pausa durável: o grafo para aqui até `approval.decided` (Command(resume=...)), mesmo após reinício."""
    case = _case(state)
    ap = case.aprovacao or {}
    info = interrupt({"aprovacao_id": ap.get("aprovacao_id"), "case_id": case.id, "titulo": ap.get("titulo"),
                      "valor": case.decisao.get("valor"), "acao_proposta": case.decisao.get("acao")})
    await _registrar_decisao(case, info if isinstance(info, dict) else {"decisao": info})
    return {}


async def _registrar_decisao(case: Case, info: dict) -> None:
    d = info.get("decisao")
    ap = case.aprovacao
    ap.update({"status": {"aprovar": "aprovada", "negar": "negada", "ajustar": "ajustada"}.get(d, "decidida"),
               "decisao": d, "aprovador": info.get("aprovador"), "canal": info.get("canal"),
               "ajuste": info.get("ajuste") or {}, "tempo_resposta_s": info.get("tempo_resposta_s"),
               "decidida_em": now(), "concordou": info.get("concordou"),
               "aprovador_id": info.get("aprovador_id"), "aprovador_cargo": info.get("aprovador_cargo")})
    if info.get("recomendacao"):
        ap["recomendacao"] = info["recomendacao"]
    _aprender(case)
    dec = case.decisao
    async with passo(case, "maestro", "retomando com a decisão humana", novo=True) as s:
        quem = f"{ap.get('aprovador') or 'supervisor'} via {ap.get('canal') or 'painel'}"
        if d == "ajustar":
            aj = ap["ajuste"] or {}
            if aj.get("valor") is not None:
                dec["valor_original"], dec["valor"] = dec.get("valor"), float(aj["valor"])
            if aj.get("acao"):
                dec["acao_original"], dec["acao"] = dec.get("acao"), aj["acao"]
            s.titulo = f"ajustado por {quem}: {dec['acao'].replace('_', ' ')}" + (f" {brl(dec['valor'])}" if dec.get("valor") else "")
        elif d == "aprovar":
            s.titulo = f"aprovado por {quem}"
        else:
            s.titulo, s.status = f"negado por {quem}", "alerta"
        if case.responsavel:
            s.titulo += f" · registrado; o caso segue com {case.responsavel.get('nome')}"
        if ap.get("tempo_resposta_s") is not None:
            s.detalhe = f"Decisão em {ap['tempo_resposta_s']} s."
        s.evidencias.append({"tipo": "aprovacao", "titulo": f"aprovação {ap.get('aprovacao_id')}: {d}",
                             "detalhe": json.dumps(ap.get("ajuste") or {}, ensure_ascii=False)})
    m = next((s for s in reversed(case.timeline) if s.agent == "mensageiro"), None)
    if m:
        m.status = "ok" if d in ("aprovar", "ajustar") else "alerta"
        m.titulo = f"aprovação respondida: {d}"
    if not case.responsavel:
        await mascote(case, "investigando")


def rota_aprovacao(state: Estado) -> str:
    case = _case(state)
    if _com_humano(case):
        return END
    return "executor" if (case.aprovacao or {}).get("decisao") in ("aprovar", "ajustar") else "negar"


async def no_executor(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    _marcar(case, "acao", "rodando", ramo="executor")
    await executor.run(case)
    acao = case.decisao.get("acao")
    ok = (case.decisao.get("execucao") or {}).get("ok", acao in ("responder", "informar_status"))
    _marcar(case, "acao", "ok" if ok else "falhou")
    return {}


async def no_negar(state: Estado) -> dict:
    case = _case(state)
    st = case.step("executor")
    st.status, st.titulo = "pulado", "negado pelo supervisor"
    case.decisao["negado"] = True
    await store.save(case)
    return {}


async def no_humano(state: Estado) -> dict:
    case = _case(state)
    if _com_humano(case):
        return {}
    dec = case.decisao
    dec["congelado"] = True
    _marcar(case, "acao", "ok", ramo="humano")
    fraude = dec.get("acao") == "negar"
    async with passo(case, "maestro", "congelando ações e chamando uma pessoa", novo=True) as s:
        assunto = ("Suspeita de evidência fabricada — supervisor decide" if fraude
                   else "Analista sênior: " + ", ".join((case.triagem or {}).get("gatilhos") or ["caso crítico"]))
        if not dec.get("chamado_humano"):
            try:
                ch = await tools.open_internal_ticket(case, assunto)
                dec["chamado_humano"] = ch
            except Exception as e:
                s.detalhe = f"falha ao abrir chamado: {e}"
        ch = dec.get("chamado_humano") or {}
        if ch:
            s.evidencias.append({"tipo": "chamado", "titulo": f"chamado {ch.get('chamado_id')}", "detalhe": assunto,
                                 "ref": f"/api/chamados/{ch.get('chamado_id')}"})
        sr = case.sugestao_responsavel
        if sr:
            s.evidencias.append({"tipo": "sugestao", "titulo": f"sugestão: {sr['nome']} ({sr['cargo']})",
                                 "detalhe": sr["motivo"]})
        s.status = "alerta"
        s.titulo = ("reclamação não procede: ações congeladas, supervisor revisa" if fraude
                    else "humano assume: ações congeladas, analista sênior acionado")
    st = case.step("executor")
    st.status, st.titulo = "pulado", "congelado pela política (nível D)"
    return {}


async def no_voz(state: Estado) -> dict:
    """Sempre responde ao cliente (se ninguém humano estiver com o caso) e fecha com o status certo."""
    case = _case(state)
    if _com_humano(case):
        return {}
    dec = case.decisao or {}
    acao, nivel = dec.get("acao"), dec.get("nivel")
    if not dec:                                       # sem pedido nem cliente
        await voz.run(case, "pedir_pedido")
        await _fechar(case, "em_andamento", "parado", "aguardando o número do pedido")
        return {}
    if acao == "solicitar_foto":
        _marcar(case, "acao", "ok", ramo="voz")
        await voz.run(case, "pedir_foto")
        _marcar(case, "voz", "ok")
        await _fechar(case, "em_andamento", "parado", "aguardando foto do cliente")
        return {}
    if dec.get("negado") or dec.get("negado_por_humano"):
        if dec.get("negado_por_humano"):
            st = case.step("executor")
            st.status, st.titulo = "pulado", "negado por quem devolveu o caso"
        await voz.run(case, "negado")
        _marcar(case, "voz", "ok")
        await _fechar(case, "rejeitado", "parado", "negado por decisão humana")
        return {}
    if nivel == "D" and dec.get("congelado"):
        fraude = acao == "negar"
        await voz.run(case, "em_analise" if fraude else "humano")
        _marcar(case, "voz", "ok")
        if fraude:
            await _fechar(case, "rejeitado", "fraude", "reclamação não procede (evidência objetiva); supervisor revisa")
        else:
            await _fechar(case, "humano", "humano", "humano assumiu; ações congeladas")
        return {}
    ok = (dec.get("execucao") or {}).get("ok", acao in ("responder", "informar_status"))
    await voz.run(case, "resolvido" if ok else "falha")
    _marcar(case, "voz", "ok")
    if ok:
        rot = "nível C aprovado" if nivel == "C" else f"nível {nivel}"
        await _fechar(case, "resolvido", "resolvido", f"{rot}: {(acao or '').replace('_', ' ')}")
    else:
        await _fechar(case, "erro", "parado", f"falha ao executar {acao}")
    return {}


# ------------------------------------------------------------------ montagem

def construir(checkpointer=None):
    g = StateGraph(Estado)
    g.add_node("inicio", no_inicio)
    g.add_node("porteiro", no_porteiro)
    g.add_node("rastreador", no_rastreador)
    g.add_node("detetive_prep", no_detetive_prep)
    g.add_node("analista", no_analista)
    g.add_node("detetive", no_detetive)
    g.add_node("politica", no_politica)
    g.add_node("revisor", no_revisor)
    g.add_node("pedir_aprovacao", no_pedir_aprovacao)
    g.add_node("aprovacao", no_aprovacao)
    g.add_node("executor", no_executor)
    g.add_node("negar", no_negar)
    g.add_node("humano", no_humano)
    g.add_node("voz", no_voz)

    g.add_conditional_edges(START, rota_inicio, {"inicio": "inicio", "politica": "politica"})
    g.add_edge("inicio", "porteiro")
    g.add_conditional_edges("porteiro", rota_porteiro, ["rastreador", "detetive_prep", "analista", "voz", END])
    g.add_edge(["rastreador", "detetive_prep"], "detetive")     # o Detetive pontua com o dossiê pronto
    g.add_edge(["detetive", "analista"], "politica")
    g.add_conditional_edges("politica", rota_politica, ["executor", "revisor", "negar", "voz", END])
    g.add_conditional_edges("revisor", rota_revisor, ["humano", "pedir_aprovacao", END])
    g.add_edge("pedir_aprovacao", "aprovacao")
    g.add_conditional_edges("aprovacao", rota_aprovacao, ["executor", "negar", END])
    g.add_edge("executor", "voz")
    g.add_edge("negar", "voz")
    g.add_edge("humano", "voz")
    g.add_edge("voz", END)
    return g.compile(checkpointer=checkpointer)


def descrever() -> dict:
    gr = (_graph or construir()).get_graph()
    return {"mermaid": gr.draw_mermaid(),
            "nos": [{"id": n.id, "nome": n.name} for n in gr.nodes.values()],
            "arestas": [{"de": e.source, "para": e.target, "condicional": e.conditional} for e in gr.edges],
            "checkpointer": "AsyncSqliteSaver", "checkpoints": str(CHECKPOINTS)}


# ------------------------------------------------------------------ execução

def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _executar(case: Case, entrada: Any, thread_id: str) -> None:
    try:
        await _graph.ainvoke(entrada, _cfg(thread_id))
    except Exception as e:
        log.exception("grafo falhou no caso %s", case.id)
        case.decisao.setdefault("erro", f"{type(e).__name__}: {e}")
        try:
            if not case.resposta.get("message_id") and not case.responsavel:
                await voz.run(case, "falha")
        except Exception:
            pass
        if not case.responsavel:
            await _fechar(case, "erro", "parado", f"falha: {type(e).__name__}")


async def iniciar(case_id: str, *, entrada: str = "inicio", sinal_humano: dict | None = None) -> None:
    """Roda o grafo do começo (novo caso) ou a partir da política (devolução humana)."""
    await garantir()
    case = store.get(case_id)
    if case is None:
        return
    async with _lock(case_id):
        n = int(case.grafo.get("execucoes") or 0)
        tid = case_id if n == 0 else f"{case_id}~{n}"
        case.grafo = {**case.grafo, "thread_id": tid, "execucoes": n + 1, "entrada": entrada}
        await store.save(case)
        await _executar(case, {"case_id": case_id, "entrada": entrada, "sinal_humano": sinal_humano}, tid)


async def retomar_aprovacao(aprovacao_id: str, info: dict, case_id: str | None = None) -> None:
    await garantir()
    case = store.get(case_id) if case_id else None
    if case is None or (case.aprovacao or {}).get("aprovacao_id") != aprovacao_id:
        case = next((c for c in store.all() if (c.aprovacao or {}).get("aprovacao_id") == aprovacao_id), None)
    if case is None:
        return
    async with _lock(case.id):
        ap = case.aprovacao or {}
        if ap.get("decisao"):
            return                                     # já registrada (idempotente)
        t = _sla.pop(case.id, None)
        if t:
            t.cancel()
        tid = ap.get("thread_id") or case.grafo.get("thread_id") or case.id
        st = await _graph.aget_state(_cfg(tid))
        pendente = any(getattr(tk, "interrupts", None) for tk in (st.tasks or ())) if st else False
        if pendente:
            await _executar(case, Command(resume=info), tid)
            return
    # sem checkpoint (caso criado antes do LangGraph): registra e segue a partir da política
    await _registrar_decisao(case, info)
    await iniciar(case.id, entrada="politica")


def _iniciar_sla(case: Case, segundos: float | None = None) -> None:
    async def vigia():
        try:
            await asyncio.sleep(config.APPROVAL_SLA_S if segundos is None else max(0.0, segundos))
            ap = case.aprovacao or {}
            if case.status in ("aguardando_aprovacao", "humano") and ap.get("aprovacao_id") and not ap.get("decisao") \
                    and not ap.get("escalada_para"):
                para = await mensageiro.escalar(case)       # a aprovação continua decidível
                ja_avisou = any(m.get("situacao") == "sla" for m in case.mensagens)
                if para and not case.responsavel and not ja_avisou:
                    await voz.run(case, "sla")               # uma vez só
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("SLA do caso %s falhou", case.id)
    t = _sla.pop(case.id, None)
    if t:
        t.cancel()
    _sla[case.id] = asyncio.create_task(vigia())


def rearmar_sla() -> int:
    """O SLA mudou (POST /demo/sla): recalcula o prazo das aprovações pendentes a partir de quando foram pedidas."""
    n = 0
    for case in store.all():
        ap = case.aprovacao or {}
        if not ap.get("aprovacao_id") or ap.get("decisao") or ap.get("escalada_para"):
            continue
        try:
            pedida = datetime.fromisoformat(ap.get("solicitada_em") or "")
        except ValueError:
            pedida = datetime.now()
        fim = pedida + timedelta(seconds=config.APPROVAL_SLA_S)
        ap["expira_em"] = fim.isoformat(timespec="seconds")
        _iniciar_sla(case, (fim - datetime.now()).total_seconds())
        n += 1
    return n


def cancelar_tudo() -> None:
    for t in list(_sla.values()):
        t.cancel()
    _sla.clear()
    _locks.clear()


def _aprender(case: Case) -> None:
    """Cada decisão humana vira exemplo (SPEC §6). Proposta de ajuste só entra após os cenários sintéticos."""
    try:
        reg = {"ts": now(), "case_id": case.id, "politica_versao": case.decisao.get("politica_versao"),
               "nivel": case.decisao.get("nivel"), "acao": case.decisao.get("acao"), "valor": case.decisao.get("valor"),
               "score": (case.veracidade or {}).get("score"), "decisao_humana": case.aprovacao.get("decisao"),
               "ajuste": case.aprovacao.get("ajuste")}
        with open(config.DATA_DIR / "aprendizado.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def apagar_threads(case_ids: list[str]) -> None:
    if _graph is None or _graph.checkpointer is None:
        return
    for cid in case_ids:
        for tid in [cid] + [f"{cid}~{i}" for i in range(1, 6)]:
            try:
                await _graph.checkpointer.adelete_thread(tid)
            except Exception:
                pass


async def garantir() -> None:
    if _graph is None:
        await startup(recarregar=False)


async def startup(recarregar: bool = True) -> None:
    """Abre o checkpointer, compila o grafo, recarrega os casos e retoma o que estava no meio."""
    global _graph, _conn
    if _graph is not None:
        return
    CHECKPOINTS.parent.mkdir(parents=True, exist_ok=True)
    _conn = await aiosqlite.connect(str(CHECKPOINTS))
    saver = AsyncSqliteSaver(_conn)
    await saver.setup()
    _graph = construir(saver)
    # o fan-out roda em superpassos: o Detetive espera o ramo mais lento. Aquece o cache do Analista já.
    try:
        from . import analista
        _bg(asyncio.to_thread(analista.insights))
    except Exception:
        pass
    if not recarregar:
        return
    n = store.carregar_do_banco()
    retomados = 0
    for case in store.all():
        if case.status == "aguardando_aprovacao" and (case.aprovacao or {}).get("aprovacao_id") \
                and not case.aprovacao.get("decisao"):
            try:
                exp = datetime.fromisoformat(case.aprovacao.get("expira_em") or "")
                _iniciar_sla(case, (exp - datetime.now()).total_seconds())
            except ValueError:
                _iniciar_sla(case)
        elif case.status == "em_andamento" and case.grafo.get("thread_id") and not case.responsavel:
            st = await _graph.aget_state(_cfg(case.grafo["thread_id"]))
            if st and st.next and not any(getattr(tk, "interrupts", None) for tk in st.tasks or ()):
                retomados += 1
                _bg(_retomar_em_andamento(case))
    log.info("LangGraph pronto (%s): %s casos recarregados, %s retomados", CHECKPOINTS, n, retomados)


async def _retomar_em_andamento(case: Case) -> None:
    async with _lock(case.id):
        await _executar(case, None, case.grafo["thread_id"])


async def shutdown() -> None:
    global _graph, _conn
    cancelar_tudo()
    if _conn is not None:
        await _conn.close()
    _graph, _conn = None, None
