"""Humano no caso: passar para uma pessoa, conversar com o cliente, devolver ao Goxy.

- `passar`: grava `case.responsavel`, status `humano`, congela as ações do Goxy (aprovação pendente continua
  válida), gera `resumo_passagem` (3 linhas) e publica `case.handoff`.
- `enviar_humano`: a pessoa responde ao cliente pelo canal de origem (via /api/mensagens).
- `mensagem_cliente`: o cliente escreveu de novo num caso com uma pessoa (ex.: Telegram) → entra na conversa.
- `devolver`: limpa o responsável; "seguir" retoma o grafo a partir da política com a nota como sinal humano,
  "encerrar" marca resolvido. Publica `case.returned`.
"""
from __future__ import annotations

import json
import re

import tools
from core import config, llm
from core.bus import bus
from core.state import Case, now, store

from . import equipe, grafo, voz
from .base import brl, como, mascote, passo
from .porteiro import ROTULO


class ErroPassagem(Exception):
    def __init__(self, status: int, msg: str):
        self.status = status
        super().__init__(msg)


# ------------------------------------------------------------------ resumo de passagem

def _resumo_template(case: Case) -> str:
    t, d, v = case.triagem or {}, case.decisao or {}, case.veracidade or {}
    pedido = (case.dossie or {}).get("pedido") or {}
    quer = ROTULO.get(t.get("intencao"), t.get("intencao") or "atendimento")
    if t.get("pede_reembolso"):
        quer += " com reembolso"
    prod = pedido.get("descricao_produto")
    l1 = f"Cliente quer: {quer}" + (f" no pedido {case.order_id}" if case.order_id else "") + \
         (f" ({prod}, {brl(pedido.get('valor'))})" if prod else "") + "."
    if d.get("porque"):
        l2 = f"Goxy achou: {d['porque']}"
    elif v.get("score") is not None:
        l2 = f"Goxy achou: veracidade {v['score']}/100 ({v.get('faixa')})."
    else:
        l2 = "Goxy achou: ainda estava investigando o caso."
    if t.get("gatilhos"):
        l2 = l2.rstrip(".") + f"; alerta: {', '.join(t['gatilhos'])}."
    ap = case.aprovacao or {}
    if ap.get("aprovacao_id") and not ap.get("decisao"):
        l3 = f"Falta decidir: aprovar, negar ou ajustar \"{ap.get('titulo') or d.get('acao')}\" (aprovação {ap['aprovacao_id']})."
    elif d.get("nivel") == "D" and d.get("acao") == "humano":
        sug = (d.get("acao_sugerida") or "").replace("_", " ")
        l3 = "Falta decidir: como responder ao cliente" + (f" e se faz o {sug}" if sug else "") + "; ações estão congeladas."
    elif case.status == "rejeitado":
        l3 = "Falta decidir: confirmar a negativa (o cliente só sabe que está em análise)."
    elif case.status == "resolvido":
        l3 = "Falta decidir: nada pendente; só acompanhar o cliente."
    else:
        acao = (d.get("acao") or "a ação").replace("_", " ")
        l3 = f"Falta decidir: se segue com {acao}" + (f" de {brl(d['valor'])}" if d.get("valor") else "") + "."
    return "\n".join([l1, l2, l3])


async def resumo_passagem(case: Case) -> str:
    base = _resumo_template(case)
    if not llm.enabled():
        return base
    try:
        txt = await llm.chat([
            {"role": "system", "content": "Reescreva este resumo de passagem de caso em EXATAMENTE 3 linhas, curtas, "
             "começando por 'Cliente quer:', 'Goxy achou:' e 'Falta decidir:'. Use só os fatos dados, mantenha números "
             "e ids. Sem CPF, cartão ou telefone. Responda só com as 3 linhas."},
            {"role": "user", "content": base},
        ], case=case, agent="maestro", max_tokens=2000, temperature=0.2)
        linhas = [x.strip() for x in (txt or "").strip().splitlines() if x.strip()]
        if len(linhas) == 3 and linhas[0].startswith("Cliente quer") and linhas[2].startswith("Falta decidir"):
            return tools.mascarar_pii("\n".join(linhas))
    except Exception:
        pass
    return base


# ------------------------------------------------------------------ passar / devolver

def _caso(case_id: str) -> Case:
    c = store.get(case_id)
    if c is None:
        raise ErroPassagem(404, "caso não encontrado")
    return c


async def passar(case_id: str, para: str, nota: str | None = None) -> Case:
    case = _caso(case_id)
    pessoa = equipe.get(para)
    if pessoa is None:
        raise ErroPassagem(422, f"colaborador {para} não existe ou não está ativo")
    anterior = case.responsavel
    case.responsavel = {"colaborador_id": pessoa["colaborador_id"], "nome": pessoa["nome"], "cargo": pessoa["cargo"],
                        "desde": now(), "nota": nota, "status_anterior": (anterior or {}).get("status_anterior") or case.status}
    case.resumo_passagem = await resumo_passagem(case)
    async with passo(case, "maestro", f"Maestro passou o caso para {pessoa['nome']}", novo=True, atraso=False) as s:
        s.status = "alerta"
        s.detalhe = case.resumo_passagem + (f"\nNota: {nota}" if nota else "")
        s.evidencias.append({"tipo": "passagem", "titulo": f"{pessoa['nome']} ({pessoa['cargo']})",
                             "detalhe": "Ações do Goxy congeladas; aprovação pendente continua válida."})
    await store.set_status(case, "humano", "humano")
    await bus.publish("case.handoff", {"para": pessoa["colaborador_id"], "nome": pessoa["nome"], "cargo": pessoa["cargo"],
                                       "resumo": case.resumo_passagem, "nota": nota}, case_id=case.id, agent="maestro")
    return case


RX_APROVA = re.compile(r"pode (fazer|seguir|mandar|enviar|reenviar|reembolsar|aprovar|liberar|trocar|abrir)|aprovad|aprovo|"
                       r"autoriz|liberad|ok (para|pra)|segue com|seguir com|faz(er)? o|procede\b", re.I)
RX_NEGA = re.compile(r"n[aã]o (fa[cç]a|fazer|reembols|reenvi|procede|aprov|libera)|negad|nego\b|recus|improcedente|"
                     r"n[aã]o tem direito", re.I)
ACOES_NOTA = [("reembolso", r"reembols|estorn|dinheiro de volta"), ("nova_producao", r"nova produ|reimprim"),
              ("troca", r"\btroca"), ("reenvio", r"reenvi|mandar outr|enviar outr"),
              ("chamado_transportadora", r"chamado|transportadora")]


def interpretar_nota(nota: str | None) -> dict:
    """Nota humana → sinal para a política. Negação vence aprovação ("não pode fazer o reenvio")."""
    txt = (nota or "").strip()
    if not txt:
        return {}
    tipo = "negar" if RX_NEGA.search(txt) else ("aprovar" if RX_APROVA.search(txt) else None)
    out: dict = {"nota": txt}
    if tipo:
        out["tipo"] = tipo
    acao = next((a for a, rx in ACOES_NOTA if re.search(rx, txt, re.I)), None)
    if acao and tipo == "aprovar":
        out["acao"] = acao
    m = re.search(r"R\$\s*([\d.]+(?:,\d{1,2})?)", txt)
    if m and tipo == "aprovar":
        out["valor"] = float(m.group(1).replace(".", "").replace(",", "."))
    return out


async def devolver(case_id: str, nota: str | None, instrucao: str = "seguir") -> Case:
    case = _caso(case_id)
    if not case.responsavel:
        raise ErroPassagem(409, "o caso não está com uma pessoa")
    if instrucao not in ("seguir", "encerrar"):
        raise ErroPassagem(422, "instrucao deve ser 'seguir' ou 'encerrar'")
    resp = case.responsavel
    case.responsavel = None
    sinal = interpretar_nota(nota)
    sinal.update({"por": resp["colaborador_id"], "nome": resp["nome"], "cargo": resp["cargo"]})
    async with passo(case, "maestro", f"{resp['nome']} devolveu o caso", novo=True, atraso=False) as s:
        s.detalhe = (f"Nota: {nota}" if nota else "Sem nota.") + f"\nInstrução: {instrucao}"
        if sinal.get("tipo"):
            s.evidencias.append({"tipo": "sinal_humano", "titulo": f"nota lida como: {sinal['tipo']}"
                                 + (f" {sinal['acao'].replace('_', ' ')}" if sinal.get("acao") else ""),
                                 "detalhe": json.dumps(sinal, ensure_ascii=False)})
    await bus.publish("case.returned", {"por": resp["colaborador_id"], "nome": resp["nome"], "nota": nota,
                                        "instrucao": instrucao, "sinal": sinal}, case_id=case.id, agent="maestro")
    try:
        with open(config.DATA_DIR / "aprendizado.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), "case_id": case.id, "decisao_humana": f"devolver:{instrucao}",
                                "sinal": sinal, "nivel": case.decisao.get("nivel"), "acao": case.decisao.get("acao"),
                                "politica_versao": case.decisao.get("politica_versao")}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if instrucao == "encerrar":
        await store.set_status(case, "resolvido", "resolvido")
        await bus.publish("case.closed", {"status": "resolvido", "resumo": f"encerrado por {resp['nome']}"},
                          case_id=case.id, agent="maestro")
        return case
    case.status = "em_andamento"
    case.encerrado_em = None
    await mascote(case, "investigando")
    grafo._bg(grafo.iniciar(case.id, entrada="politica", sinal_humano=sinal))
    return case


# ------------------------------------------------------------------ conversa

async def enviar_humano(case_id: str, texto: str, autor: str) -> dict:
    case = _caso(case_id)
    pessoa = equipe.get(autor)
    if pessoa is None:
        raise ErroPassagem(422, f"colaborador {autor} não existe ou não está ativo")
    texto = tools.mascarar_pii((texto or "").strip())
    if not texto:
        raise ErroPassagem(422, "texto vazio")
    with como(pessoa["colaborador_id"]):
        r = await tools.send_message(case, texto)
    msg = {"autor": "agente", "nome": pessoa["nome"], "colaborador_id": pessoa["colaborador_id"], "texto": texto,
           "ts": now(), "canal": case.canal, "message_id": r.get("message_id")}
    case.mensagens.append(msg)
    await store.save(case)
    return msg


async def mensagem_cliente(case_id: str, texto: str, anexos: list[dict] | None = None) -> dict:
    case = _caso(case_id)
    msg = {"autor": "cliente", "texto": (texto or "").strip() or "[foto anexada]", "ts": now(), "canal": case.canal}
    if anexos:
        from core.state import Anexo
        novos = [Anexo(**{k: v for k, v in a.items() if k in Anexo.model_fields}) for a in anexos]
        case.anexos.extend(novos)
        msg["anexos"] = [a.anexo_id or a.url for a in novos]
    case.mensagens.append(msg)
    await store.save(case)
    await bus.publish("message.received", {"canal": case.canal, "texto": msg["texto"], "autor": "cliente",
                                           "customer_id": case.customer_id}, case_id=case.id, agent="cliente")
    await _retomar_se_esperava(case, msg["texto"], bool(anexos))
    return msg


async def _retomar_se_esperava(case: Case, texto: str, chegou_foto: bool) -> None:
    """Caso em `aguardando_cliente`: o número do pedido (ou a foto pedida) retoma o grafo pela investigação."""
    from .porteiro import RX_ORDER
    if case.status != "aguardando_cliente" or case.responsavel:
        return
    m = re.search(RX_ORDER, (texto or "").upper())
    esperava_foto = (case.decisao or {}).get("acao") == "solicitar_foto"
    if not m and not (chegou_foto and esperava_foto):
        return
    if m:
        case.order_id = m.group(0)
        case.dossie.pop("pedido_nao_encontrado", None)
    case.status, case.encerrado_em = "em_andamento", None
    await mascote(case, "investigando")
    async with passo(case, "maestro", "o cliente respondeu: retomando a investigação", novo=True, atraso=False) as s:
        s.detalhe = (f"Pedido informado: {case.order_id}." if m else "Foto recebida.")
    grafo._bg(grafo.iniciar(case.id, entrada="investigar"))


async def sugerir_resposta(case_id: str) -> str:
    return await voz.rascunhar(_caso(case_id))
