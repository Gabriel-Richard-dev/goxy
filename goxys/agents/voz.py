"""Voz (comunicação): redige e envia a resposta no canal de origem, no tom Gocase.

Curta, calorosa, 2ª pessoa, verbo na frente. Nunca repete CPF/cartão/telefone, nunca acusa o cliente
(reclamação suspeita: só "está em análise"). LLM redige quando ligado; templates são o fallback.
"""
from __future__ import annotations

import re

import tools
from core import llm
from core.state import Case, now

from .base import brl, passo

PROIBIDO = re.compile(r"fraud|golpe|falsa|falsific|mentir|mentiu|suspeit|IA gerou|manipulad", re.I)


def _primeiro_nome(case: Case) -> str:
    n = (case.cliente_nome or "").strip().split()
    return n[0] if n else ""


def _ola(case: Case) -> str:
    n = _primeiro_nome(case)
    return f"Oi, {n}!" if n else "Oi!"


def template(case: Case, situacao: str) -> str:
    dec = case.decisao or {}
    ex = dec.get("execucao") or {}
    res = ex.get("resultado") or {}
    acao = dec.get("acao")
    rast = (case.dossie or {}).get("rastreio") or {}
    pedido = (case.dossie or {}).get("pedido") or {}
    ped = case.order_id or "seu pedido"
    ola = _ola(case)
    if situacao == "pedir_pedido":
        return f"{ola} Quero resolver isso pra você. Me manda o número do pedido (começa com GCH ou GBH)?"
    if situacao == "pedir_foto":
        return (f"{ola} Sinto muito pelo problema com o pedido {ped}. Me manda uma foto do produto e da embalagem? "
                "Assim que chegar, eu já resolvo por aqui.")
    reacao = (case.triagem or {}).get("intencao") == "reacao_adversa"
    suspender = (" Por segurança, suspenda o uso do produto agora e, se os sintomas piorarem ou houver falta de ar, "
                 "procure atendimento médico.")
    if reacao and situacao in ("em_analise", "resolvido"):
        return (f"{ola} Sinto muito por isso.{suspender} Já registrei seu relato com o lote do produto para a nossa "
                "responsável técnica, e nosso time vai falar com você em até 24 horas sobre os próximos passos.")
    if situacao == "em_analise":
        return (f"{ola} Recebi sua mensagem sobre o pedido {ped}. Sua solicitação está em análise com o nosso time "
                "e eu te dou retorno aqui em até 24 horas. Obrigado pela paciência!")
    if situacao == "sla":
        esc = (case.aprovacao or {}).get("escalada_para") or {}
        ate = str(esc.get("novo_prazo") or "")
        hora = ate[11:16] if len(ate) >= 16 else ""
        return (f"{ola} Passando pra te atualizar: seu caso do pedido {ped} está com um supervisor do nosso time"
                + (f" e você recebe a resposta até as {hora}" if hora else " e você recebe a resposta em breve")
                + ". Obrigado pela paciência!")
    if situacao == "desfeito":
        o_que = {"reenvio": "o reenvio", "troca": "a troca", "nova_producao": "a nova produção",
                 "reembolso": "o reembolso"}.get(acao or "", "a solução")
        return (f"{ola} Precisei rever {o_que} do pedido {ped} que te informei há pouco: ele foi suspenso por enquanto. "
                "Uma pessoa do nosso time vai falar com você ainda hoje para combinar o melhor caminho. Desculpa a confusão!")
    if situacao == "humano":
        if reacao:
            return f"{ola} Sinto muito por isso.{suspender} Uma pessoa do nosso time vai falar com você ainda hoje."
        return (f"{ola} Entendo sua insatisfação e sinto muito pelo transtorno com o pedido {ped}. Uma pessoa do nosso "
                "time de especialistas vai assumir seu atendimento pessoalmente e falar com você ainda hoje.")
    if situacao == "negado":
        nota = (case.aprovacao or {}).get("ajuste", {}) or {}
        extra = f" {nota.get('nota')}" if nota.get("nota") else ""
        return (f"{ola} Analisamos com cuidado sua solicitação do pedido {ped} e, com as informações que temos, não "
                f"conseguimos aprovar desta vez.{extra} Se quiser, me manda mais detalhes ou fotos que eu reavalio.")
    if situacao == "falha":
        return (f"{ola} Já estou cuidando do seu pedido {ped}. Tive uma instabilidade aqui no sistema e vou te "
                "confirmar a solução em breve, sem você precisar chamar de novo.")
    # resolvido
    if acao in ("reenvio", "nova_producao", "troca"):
        o_que = {"reenvio": "o reenvio", "nova_producao": "uma nova produção", "troca": "a troca"}[acao]
        novo = res.get("pedido_reposicao_id")
        return (f"{ola} Sinto muito pelo que aconteceu. Já providenciei {o_que} sem custo pra você"
                + (f" (novo pedido {novo})" if novo else "") + ". Você recebe o rastreio por aqui assim que sair. "
                "Não precisa devolver nada.")
    if acao == "chamado_transportadora":
        prot = res.get("protocolo") or res.get("chamado_id")
        transp = res.get("transportadora") or rast.get("transportadora") or pedido.get("transportadora") or "transportadora"
        ra = (" Vi também sua reclamação no Reclame Aqui e vou atualizá-la por lá com esta mesma posição."
              if (case.triagem or {}).get("reclame_aqui_aberto") else "")
        return (f"{ola} Seu pedido {ped} atrasou na {transp} e eu já abri um chamado com eles"
                + (f" (protocolo {prot})" if prot else "") + ". Estou acompanhando de perto e te aviso aqui "
                "assim que tiver novidade." + ra + " Desculpa pela espera!")
    if acao == "reembolso" and ex.get("ja_existia"):
        return (f"{ola} Conferi aqui: o reembolso de {brl(res.get('valor') or dec.get('valor'))} do pedido {ped} já está "
                "registrado, então você não precisa pedir de novo. No cartão aparece em até 2 faturas; no Pix, em até 5 dias úteis.")
    if acao == "reembolso":
        return (f"{ola} Pronto: registrei o reembolso de {brl(res.get('valor') or dec.get('valor'))} do pedido {ped}. "
                "No cartão aparece em até 2 faturas; no Pix, em até 5 dias úteis.")
    if acao == "informar_status":
        return (f"{ola} Seu pedido {ped} está {str(rast.get('status') or pedido.get('status_entrega') or 'a caminho').replace('_', ' ')}"
                + (f", com previsão para {rast.get('previsao_entrega')}" if rast.get("previsao_entrega") else "")
                + ". Qualquer novidade eu te aviso por aqui!")
    if (case.triagem or {}).get("intencao") == "elogio":
        return f"{ola} Que alegria ler isso! Obrigado por escolher a gente. 💛"
    return f"{ola} Recebi sua mensagem e já estou cuidando disso. Te respondo por aqui em instantes."


async def _redigir(case: Case, situacao: str, base: str) -> tuple[str, str]:
    if not llm.enabled():
        return base, "template"
    fatos = {"situacao": situacao, "primeiro_nome": _primeiro_nome(case), "pedido": case.order_id,
             "acao": (case.decisao or {}).get("acao"), "resultado": ((case.decisao or {}).get("execucao") or {}).get("comprovante"),
             "rascunho": base}
    try:
        txt = await llm.chat([
            {"role": "system", "content":
                "Você é a Voz do atendimento Gocase. Reescreva o rascunho no tom da marca: curto (até 3 frases), "
                "caloroso, em 2ª pessoa (você), verbo na frente, sem jargão. Mantenha TODOS os fatos do rascunho "
                "(números de pedido, protocolo, valores, prazos) e não invente nenhum outro. Nunca mencione fraude "
                "ou suspeita, nunca repita CPF, cartão ou telefone. Não presuma o gênero do cliente: use linguagem neutra (em vez de \"fique tranquila\" ou \"tranquilo\", escreva \"não se preocupe\"). Responda só com a mensagem."},
            {"role": "user", "content": str(fatos)},
        ], case=case, agent="voz", max_tokens=2000, temperature=0.4)
        txt = (txt or "").strip().strip('"')
        if not txt or len(txt) > 700 or PROIBIDO.search(txt):
            return base, "template"
        return txt, "llm"
    except Exception:
        return base, "template"


CONFIRMACAO = "Oi! Recebi sua mensagem e já estou cuidando do seu pedido. Te respondo por aqui em instantes."


async def confirmar(case: Case) -> None:
    """Confirmação imediata (SPEC §14). No Telegram o bot já mandou: só registra na conversa."""
    if any(m.get("tipo") == "confirmacao" for m in case.mensagens):
        return
    try:
        r = await tools.send_message(case, CONFIRMACAO)
        case.mensagens.append({"autor": "goxys", "nome": "Goxy", "tipo": "confirmacao", "texto": CONFIRMACAO,
                               "ts": now(), "canal": case.canal, "message_id": r.get("message_id")})
    except tools.ToolError:
        pass


async def rascunhar(case: Case) -> str:
    """Rascunho para o humano que está com o caso (não envia). Usa o caso e as últimas mensagens."""
    resp = case.responsavel or {}
    ola = _ola(case)
    eu = f" Aqui é {resp['nome'].split()[0]}, do time Gocase." if resp.get("nome") else ""
    ped = case.order_id or "seu pedido"
    dec = case.decisao or {}
    ultimas = [m for m in case.mensagens if m.get("texto")][-6:]
    ult_cli = next((m["texto"] for m in reversed(ultimas) if m.get("autor") == "cliente"), case.mensagem)
    if (case.triagem or {}).get("intencao") == "reacao_adversa":
        meio = " Por segurança, mantenha o produto sem uso. Já registrei seu relato e vou te acompanhar pessoalmente."
    elif dec.get("nivel") == "D" and dec.get("acao") == "humano":
        meio = (f" Li todo o histórico do pedido {ped} e vou cuidar pessoalmente do seu caso até o fim. "
                "Me dá só um tempinho que já te trago a solução.")
    elif case.status == "aguardando_aprovacao" or dec.get("nivel") == "C":
        meio = f" Estou analisando sua solicitação do pedido {ped} com cuidado e te dou retorno ainda hoje."
    else:
        meio = f" Estou com o seu pedido {ped} aqui e já vou te ajudar."
    base = f"{ola}{eu}{meio}"
    if not llm.enabled():
        return tools.mascarar_pii(base)
    try:
        txt = await llm.chat([
            {"role": "system", "content":
                "Você ajuda um atendente humano da Gocase a responder o cliente. Escreva UMA mensagem curta (até 3 frases), "
                "calorosa, em 2ª pessoa, em nome do atendente. Use só os fatos dados; não prometa reembolso, troca ou prazo "
                "que não estejam nos fatos; nunca mencione fraude; nunca repita CPF, cartão ou telefone. Só a mensagem."},
            {"role": "user", "content": str({"atendente": resp.get("nome"), "cliente": _primeiro_nome(case),
                                             "pedido": case.order_id, "status_do_caso": case.status,
                                             "decisao_goxys": dec.get("porque"), "ultimas_mensagens": ultimas,
                                             "ultima_do_cliente": ult_cli, "rascunho": base})},
        ], case=case, agent="voz", max_tokens=2000, temperature=0.4)
        txt = (txt or "").strip().strip('"')
        if txt and len(txt) <= 700 and not PROIBIDO.search(txt):
            return tools.mascarar_pii(txt)
    except Exception:
        pass
    return tools.mascarar_pii(base)


async def run(case: Case, situacao: str = "resolvido") -> dict:
    if case.responsavel:  # uma pessoa está com o caso: o Goxy não fala por ela
        s = case.step("voz") if not any(x.agent == "voz" for x in case.timeline) else None
        if s is not None:
            s.status, s.titulo = "pulado", f"caso com {case.responsavel.get('nome')}: a resposta é dela"
        return case.resposta
    async with passo(case, "voz", "redigindo a resposta", novo=any(s.agent == "voz" and s.status not in ("pendente",)
                                                                   for s in case.timeline)) as s:
        base = template(case, situacao)
        texto, fonte = await _redigir(case, situacao, base)
        texto = tools.mascarar_pii(texto)
        try:
            r = await tools.send_message(case, texto)
        except tools.ToolError as e:
            s.status, s.titulo, s.detalhe = "falhou", "não consegui enviar a resposta", str(e)
            case.resposta = {"texto": texto, "canal": case.canal, "erro": str(e), "situacao": situacao}
            return case.resposta
        case.resposta = {"texto": texto, "canal": r.get("canal", case.canal), "message_id": r.get("message_id"),
                         "enviado_em": r.get("enviado_em"), "situacao": situacao, "fonte": fonte}
        case.mensagens.append({"autor": "goxys", "nome": "Goxy", "texto": texto, "ts": now(), "canal": case.canal,
                               "message_id": r.get("message_id"), "situacao": situacao})
        canal = {"whatsapp": "WhatsApp", "email": "e-mail", "reclame_aqui": "Reclame Aqui",
                 "telegram": "Telegram", "painel": "painel"}.get(case.canal, case.canal)
        s.titulo = {"em_analise": f"avisou o cliente: em análise ({canal})",
                    "sla": f"atualizou o cliente sobre a espera ({canal})",
                    "humano": f"avisou que um especialista assume ({canal})",
                    "pedir_foto": f"pediu foto ao cliente ({canal})",
                    "pedir_pedido": f"pediu o número do pedido ({canal})",
                    "negado": f"respondeu com cuidado, sem acusar ({canal})",
                    "desfeito": f"avisou o cliente que a ação foi desfeita ({canal})"}.get(situacao, f"respondeu no {canal}")
        s.detalhe = texto
        s.evidencias.append({"tipo": "mensagem", "titulo": f"mensagem {r.get('message_id')}", "detalhe": texto,
                             "fonte": fonte})
        return case.resposta
