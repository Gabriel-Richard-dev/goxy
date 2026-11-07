"""Executor (resolução): age nos sistemas, mas só depois da política.

Antes de cada escrita pergunta `politica.permite_escrita`; a ferramenta revalida e lança `PolicyViolation`.
Reembolso usa Idempotency-Key = case_id + ação (nunca paga duas vezes). Nível B agenda "desfazer em 15 min"
como dado no caso (`decisao.desfazer`).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import tools
from core.state import Case

from .base import brl, passo
from .politica import carregar, permite_escrita


def _reembolso_anterior(case: Case) -> dict | None:
    hist = ((case.dossie or {}).get("historico") or {}).get("estornos") or []
    ok = [e for e in hist if e.get("order_id") == case.order_id
          and str(e.get("status", "")).lower() in ("aprovado", "concluido", "concluído")]
    return ok[0] if ok else None


async def run(case: Case) -> None:
    dec = case.decisao
    acao = dec.get("acao")
    if acao in ("responder", "informar_status", "solicitar_foto", "negar", None):
        s = case.step("executor")
        s.status, s.titulo = "pulado", "nada para executar nos sistemas"
        return
    async with passo(case, "executor", f"executando: {acao.replace('_', ' ')}") as s:
        ok, motivo = permite_escrita(dec, case.aprovacao)
        s.evidencias.append({"tipo": "politica", "titulo": f"política consultada: {motivo}",
                             "detalhe": f"versão {dec.get('politica_versao')}"})
        if not ok:
            s.status, s.titulo = "alerta", f"bloqueado pela política ({motivo})"
            return
        nivel = dec["nivel"]
        ap_id = (case.aprovacao or {}).get("aprovacao_id") if nivel == "C" else None
        order_id = case.order_id
        valor = float(dec.get("valor") or 0)
        motivo_txt = dec.get("motivo_acao") or (case.triagem or {}).get("intencao", "")
        res: dict = {}
        try:
            if acao == "cosmetovigilancia":
                reg = dec.get("cosmetovigilancia") or {}
                comprovante = reg.get("chamado_id")
                res = {"registro": reg}
                s.titulo = f"cosmetovigilância confirmada pelo supervisor ({comprovante})"
                if not comprovante:
                    raise tools.ToolError("cosmetovigilancia", None, {"erro": "registro_ausente"})
            elif acao in ("reenvio", "troca", "nova_producao"):
                res = await tools.create_exchange(case, order_id, dec.get("sku"), acao, motivo_txt,
                                                  nivel_autonomia=nivel, aprovacao_id=ap_id)
                s.titulo = (f"{acao.replace('_', ' ')} gerado: {res.get('pedido_reposicao_id') or res.get('solicitacao_id')}")
                comprovante = res.get("pedido_reposicao_id") or res.get("solicitacao_id")
            elif acao == "chamado_transportadora":
                transp = ((case.dossie or {}).get("rastreio") or {}).get("transportadora") or \
                         ((case.dossie or {}).get("pedido") or {}).get("transportadora") or "transportadora"
                res = await tools.open_carrier_ticket(case, order_id, motivo_txt, nivel_autonomia=nivel, aprovacao_id=ap_id)
                s.titulo = f"chamado aberto na {res.get('transportadora') or transp}: protocolo {res.get('protocolo') or res.get('chamado_id')}"
                comprovante = res.get("protocolo") or res.get("chamado_id")
            elif acao == "reembolso":
                res = await tools.issue_refund(case, order_id, valor, motivo_txt, ap_id, nivel_autonomia=nivel)
                s.titulo = f"reembolso de {brl(res.get('valor', valor))} registrado ({res.get('reembolso_id')})"
                if res.get("idempotente"):
                    s.titulo += " · já existia, não pagou de novo"
                comprovante = res.get("reembolso_id")
            else:
                s.status, s.titulo = "alerta", f"ação desconhecida: {acao}"
                return
        except tools.PolicyViolation as e:
            s.status, s.titulo, s.detalhe = "alerta", "a ferramenta recusou: política", str(e)
            dec["execucao"] = {"ok": False, "erro": str(e)}
            return
        except tools.ToolError as e:
            anterior = _reembolso_anterior(case) if acao == "reembolso" and isinstance(e.corpo, dict) \
                and e.corpo.get("erro") == "valor_acima_do_pedido" else None
            if anterior:  # já foi pago antes (outro contato): não paga de novo e conta como resolvido
                comprovante = anterior.get("estorno_id")
                dec["execucao"] = {"ok": True, "acao": acao, "comprovante": comprovante, "ja_existia": True,
                                   "resultado": {"reembolso_id": comprovante, "valor": anterior.get("valor"),
                                                 "status": anterior.get("status"), "idempotente": True}}
                s.status = "ok"
                s.titulo = f"reembolso já existia ({comprovante}): não paguei de novo"
                s.detalhe = str(e)
                s.evidencias.append({"tipo": "comprovante", "titulo": f"reembolso anterior {comprovante}",
                                     "detalhe": str(anterior)[:300]})
                return
            s.status, s.titulo, s.detalhe = "falhou", f"sistema recusou ({e.status})", str(e)
            dec["execucao"] = {"ok": False, "erro": str(e), "status_http": e.status}
            return
        dec["execucao"] = {"ok": True, "acao": acao, "comprovante": comprovante, "resultado": res}
        s.evidencias.append({"tipo": "comprovante", "titulo": f"comprovante {comprovante}", "detalhe": str(res)[:300],
                             "ref": f"/api/eventos?case_id={case.id}"})
        if nivel == "B":
            minutos = int(carregar()["niveis"]["B"].get("desfazer_em_min", 15))
            dec["desfazer_ate"] = (datetime.now() + timedelta(minutes=minutos)).isoformat(timespec="seconds")
            dec["desfazer"] = {"ate": dec["desfazer_ate"],
                               "minutos": minutos, "acao": acao, "comprovante": comprovante,
                               "como": f"cancelar {acao} {comprovante}", "status": "agendado",
                               "notificado": "supervisor"}
            s.evidencias.append({"tipo": "desfazer", "titulo": f"supervisor avisado · desfazer até {minutos} min",
                                 "detalhe": dec["desfazer"]["ate"]})


class PrazoExpirado(Exception):
    pass


async def desfazer(case: Case, por: str, canal: str = "painel") -> dict:
    """Nível B: cancela/estorna de verdade as escritas do caso, dentro da janela (relógio real). Idempotente."""
    from core.bus import bus
    from core.state import now, store

    dec = case.decisao or {}
    if dec.get("desfeito"):
        return dec["desfeito"]
    ate = dec.get("desfazer_ate") or (dec.get("desfazer") or {}).get("ate")
    if not ate:
        raise LookupError("este caso não tem ação desfazível (só nível B)")
    if datetime.now() > datetime.fromisoformat(ate):
        raise PrazoExpirado(f"a janela de desfazer terminou às {ate[11:16]}")
    feitas = []
    async with passo(case, "executor", "desfazendo a ação a pedido do supervisor", novo=True, atraso=False) as s:
        for a in case.acoes:
            if a.status_http != 200 or not isinstance(a.resultado, dict):
                continue
            try:
                if a.ferramenta == "create_exchange" and a.resultado.get("solicitacao_id"):
                    r = await tools.cancel_exchange(case, a.resultado["solicitacao_id"], f"desfeito por {por}")
                    feitas.append({"ferramenta": "create_exchange", "id": r.get("solicitacao_id"),
                                   "pedido_reposicao_id": r.get("pedido_reposicao_id"), "status": r.get("status")})
                elif a.ferramenta == "issue_refund" and a.resultado.get("reembolso_id") and not a.resultado.get("idempotente"):
                    r = await tools.reverse_refund(case, a.resultado["reembolso_id"], f"desfeito por {por}")
                    feitas.append({"ferramenta": "issue_refund", "id": r.get("reembolso_id"), "valor": r.get("valor"),
                                   "status": r.get("status")})
            except tools.ToolError as e:
                feitas.append({"ferramenta": a.ferramenta, "id": None, "status": "falhou", "erro": str(e)})
        dec["desfeito"] = {"por": por, "canal": canal, "ts": now(), "acoes": feitas}
        if dec.get("desfazer"):
            dec["desfazer"].update({"status": "desfeito", "por": por, "em": now()})
        s.status = "alerta"
        s.titulo = f"desfeito por {por}: " + (", ".join(f"{x['ferramenta'].replace('create_exchange', 'reposição').replace('issue_refund', 'reembolso')} {x['status']}"
                                                    for x in feitas) or "nada a cancelar")
        s.evidencias = [{"tipo": "desfeito", "titulo": f"{x['ferramenta']} {x.get('id')}", "detalhe": x["status"]} for x in feitas]
    await bus.publish("case.undone", {"por": por, "canal": canal, "acoes": feitas}, case_id=case.id, agent="executor")
    from . import voz
    await voz.run(case, "desfeito")
    await store.set_status(case, "humano", "humano")
    return dec["desfeito"]
