"""Mensageiro (escalonamento): monta o pedido de aprovação com resumo, evidências e botões.

O sistema de aprovações (POST /api/aprovacoes) publica `approval.requested` para Slack/Telegram/painel;
a decisão (POST /api/aprovacoes/{id}/decisao) publica `approval.decided` e o Maestro retoma.
Sem resposta no SLA → `approval.escalated` (supervisor) + atualização de status ao cliente.
"""
from __future__ import annotations

import tools
from core import config
from core.bus import bus
from core.state import Case
from core.state import now as now_iso

from .base import brl, passo

# O sistema de aprovações já publica approval.requested (mock_api); deixe True só com um backend que não publique.
PUBLICAR_REQUESTED = False


NOMES = {"reenvio": "Reenvio", "reembolso": "Reembolso", "troca": "Troca", "nova_producao": "Nova produção",
         "chamado_transportadora": "Chamado na transportadora", "cosmetovigilancia": "Registro de cosmetovigilância"}


def titulo_curto(case: Case) -> str:
    """Título curto, valor uma vez só ("Reembolso de R$ 689,70 com foto duvidosa"); o valor também vai em `valor`."""
    dec, v, t = case.decisao, case.veracidade or {}, case.triagem or {}
    acao = dec.get("acao") or ""
    base = NOMES.get(acao, acao.replace("_", " ").capitalize())
    if dec.get("valor"):
        base += f" de {brl(dec['valor'])}"
    if acao == "cosmetovigilancia":
        return f"{base} por reação adversa"
    if t.get("injecao"):
        return f"{base} com instrução suspeita no texto"
    if v.get("faixa") in ("amarela", "vermelha"):
        return f"{base} com {'foto duvidosa' if case.anexos else 'evidência fraca'}"
    if (dec.get("sinais") or {}).get("fora_do_prazo_7d"):
        return f"{base} fora do prazo"
    if (dec.get("sinais") or {}).get("direito_cliente"):
        return f"{base} por direito do cliente"
    return f"{base} acima da alçada"


def _resumo(case: Case) -> tuple[str, str, list[dict]]:
    dec, v, t = case.decisao, case.veracidade or {}, case.triagem or {}
    pedido = (case.dossie or {}).get("pedido") or {}
    acao = (dec.get("acao") or "").replace("_", " ")
    titulo = titulo_curto(case)
    linhas = [
        f"{case.cliente_nome or case.customer_id} · pedido {case.order_id} ({pedido.get('descricao_produto', '')})",
        f"Pedido: {t.get('intencao', '?')}" + (" com pedido de reembolso" if t.get("pede_reembolso") else ""),
        f"Proposta: {acao} de {brl(dec.get('valor'))}" if dec.get("valor") else f"Proposta: {acao}",
        f"Veracidade: {v.get('score', '—')}/100 ({v.get('faixa', '—')})",
        f"Por que precisa de você: {dec.get('porque') or dec.get('motivo')}",
        f"Regra: {dec.get('motivo')} (política {dec.get('politica_versao')})",
    ]
    rev = dec.get("revisor") or {}
    if rev.get("ressalvas"):
        linhas.append("Revisor: " + "; ".join(rev["ressalvas"]))
    custo = (dec.get("sinais") or {}).get("custo_erro") or {}
    if custo.get("leitura"):
        linhas.append("Custo de errar: " + custo["leitura"])
    evid = []
    for e in sorted(v.get("evidencias") or [], key=lambda e: e.get("peso", 0))[:5]:
        evid.append({"titulo": e["titulo"], "detalhe": e.get("detalhe", ""), "peso": e.get("peso")})
    if v.get("comparacao"):
        evid.insert(0, {"titulo": "comparação lado a lado", "comparacao": v["comparacao"]})
    return titulo, "\n".join(linhas), evid


def recomendacao(case: Case) -> dict:
    """O que o Goxy recomendaria a quem decide (base da métrica de concordância Goxy × pessoas)."""
    dec, v, t = case.decisao, case.veracidade or {}, case.triagem or {}
    negar, motivo = False, "evidência confere"
    if v.get("fraude_objetiva"):
        negar, motivo = True, "evidência objetiva de fraude"
    elif t.get("injecao"):
        negar, motivo = True, "texto tenta dar ordens ao agente"
    elif v.get("faixa") == "vermelha":
        negar, motivo = True, f"veracidade baixa ({v.get('score')}/100)"
    elif v.get("faixa") == "amarela":
        motivo = f"veracidade na faixa cinzenta ({v.get('score')}/100); o custo de travar um honesto é maior"
    elif dec.get("acao") == "cosmetovigilancia":
        motivo = "registro obrigatório de reação adversa"
    return {"acao": dec.get("acao"), "valor": dec.get("valor"), "decisao_sugerida": "negar" if negar else "aprovar",
            "motivo": motivo}


async def run(case: Case, *, solicitante: str | None = None) -> dict:
    async with passo(case, "mensageiro", "montando o pedido de aprovação") as s:
        titulo, resumo, evid = _resumo(case)
        dec = case.decisao
        rec = recomendacao(case)
        r = await tools.request_approval(case, titulo=titulo, resumo=resumo, nivel=dec["nivel"],
                                         valor=dec.get("valor"), acao_proposta=dec.get("acao"), evidencias=evid,
                                         prazo_s=config.APPROVAL_SLA_S, recomendacao=rec, solicitante=solicitante)
        case.aprovacao = {"aprovacao_id": r.get("aprovacao_id"), "status": r.get("status", "pendente"),
                          "expira_em": r.get("expira_em"), "titulo": titulo, "resumo": resumo, "valor": dec.get("valor"),
                          "solicitada_em": r.get("criado_em") or r.get("solicitada_em"),
                          "solicitante": r.get("solicitante") or solicitante or "mensageiro", "recomendacao": rec}
        if PUBLICAR_REQUESTED:
            await bus.publish("approval.requested", {
                "aprovacao_id": r.get("aprovacao_id"), "titulo": titulo, "resumo": resumo, "nivel": dec["nivel"],
                "valor": dec.get("valor"), "acao_proposta": dec.get("acao"), "evidencias": evid,
                "expira_em": r.get("expira_em"), "telegram_chat_id": case.telegram_chat_id,
            }, case_id=case.id, agent="mensageiro")
        s.status = "alerta"
        s.titulo = f"pediu aprovação: {titulo}"
        s.detalhe = resumo + f"\nRecomendação do Goxy: {rec['decisao_sugerida']} ({rec['motivo']})"
        s.evidencias.append({"tipo": "aprovacao", "titulo": f"aprovação {r.get('aprovacao_id')}",
                             "detalhe": "Aprovar, Negar ou Ajustar no Slack, Telegram ou painel",
                             "ref": f"/api/aprovacoes/{r.get('aprovacao_id')}"})
        return case.aprovacao


async def escalar(case: Case) -> dict | None:
    """SLA estourado: sobe a outro supervisor (nunca quem pediu nem quem já estava sugerido) e registra.
    A aprovação continua decidível; a Voz avisa o cliente uma vez (quem chama)."""
    from datetime import datetime, timedelta

    from . import equipe
    ap = case.aprovacao or {}
    if ap.get("escalada_para"):
        return None
    excluir = {(case.sugestao_responsavel or {}).get("colaborador_id"), (case.responsavel or {}).get("colaborador_id"),
               ap.get("solicitante")}
    sup = next((p for p in equipe.listar() if p["cargo"] == "supervisor" and p["colaborador_id"] not in excluir), None) \
        or next((p for p in equipe.listar() if p["cargo"] == "supervisor"), None)
    para = {"colaborador_id": sup["colaborador_id"], "nome": sup["nome"]} if sup else {"colaborador_id": None,
                                                                                      "nome": "supervisão"}
    novo_prazo = (datetime.now() + timedelta(seconds=max(config.APPROVAL_SLA_S, 60))).isoformat(timespec="minutes")
    ap.update({"escalada": True, "escalada_para": {**para, "em": now_iso(), "novo_prazo": novo_prazo}})
    async with passo(case, "mensageiro", f"Mensageiro escalou para {para['nome']}", novo=True, atraso=False) as s:
        await bus.publish("approval.escalated", {"aprovacao_id": ap.get("aprovacao_id"), "para": para,
                                                 "novo_prazo": novo_prazo}, case_id=case.id, agent="mensageiro")
        s.status = "alerta"
        s.detalhe = f"Sem resposta no prazo. A aprovação {ap.get('aprovacao_id')} continua aberta e foi para {para['nome']}."
    return para
