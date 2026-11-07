"""Formatadores puros (sem rede) das mensagens dos canais. Testados em tests/test_channels.py."""
from __future__ import annotations

import html
import re
from typing import Any

NIVEL_ROTULO = {
    "A": "A · autônomo",
    "B": "B · autônomo com aviso",
    "C": "C · aprovação obrigatória",
    "D": "D · humano assume",
}
DECISAO_ROTULO = {"aprovar": "✅ Aprovado", "negar": "⛔ Negado", "ajustar": "✏️ Ajustado"}
ACOES = ("aprovar", "negar", "ajustar")
PEDIDO_RE = re.compile(r"\b(G[CB]H\d{5,})\b", re.I)
VALOR_RE = re.compile(r"(?:r\$\s*)?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})|\d+(?:[.,]\d{1,2})?)", re.I)


def brl(v: Any) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def extrair_pedido(texto: str | None) -> str | None:
    m = PEDIDO_RE.search(texto or "")
    return m.group(1).upper() if m else None


def parse_ajuste(texto: str | None) -> dict:
    """'350 reembolso parcial' / 'R$ 1.250,50 ok' → {valor: 350.0, nota: '...'}; sem número → só nota."""
    texto = (texto or "").strip()
    m = VALOR_RE.search(texto)
    if not m:
        return {"nota": texto} if texto else {}
    bruto = m.group(1)
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    try:
        valor = float(bruto)
    except ValueError:
        return {"nota": texto}
    nota = (texto[:m.start()] + texto[m.end():]).replace("R$", "").strip(" -:;,.")
    out: dict = {"valor": valor}
    if nota:
        out["nota"] = nota
    return out


def evidencia_texto(e: Any) -> str:
    """Evidências chegam como str ou dict de formato livre; vira uma linha curta."""
    if e is None:
        return ""
    if isinstance(e, str):
        return e
    if isinstance(e, dict):
        for k in ("texto", "resumo", "descricao", "titulo", "motivo", "detalhe"):
            if e.get(k):
                pre = e.get("tipo") or e.get("fonte")
                return f"{pre}: {e[k]}" if pre and pre != e[k] else str(e[k])
        return ", ".join(f"{k}: {v}" for k, v in list(e.items())[:4] if v not in (None, "", [], {}))
    return str(e)


def _evidencias(data: dict, limite: int = 5) -> list[str]:
    ev = [evidencia_texto(e) for e in (data.get("evidencias") or [])]
    ev = [x[:180] for x in ev if x]
    return ev[:limite]


def _expira(data: dict) -> str:
    e = data.get("expira_em") or ""
    return str(e)[11:16] if len(str(e)) >= 16 else str(e)


# ------------------------------------------------------------------ Telegram (HTML)

def telegram_confirmacao() -> str:
    return "Recebi! Já estou olhando seu pedido."


def telegram_boas_vindas() -> str:
    return ("Oi! Eu sou o <b>Goxy</b>, a equipe de agentes de atendimento da Gocase e GoBeauté.\n\n"
            "Me conte o que aconteceu com seu pedido (de preferência com o número, ex.: <code>GCH123456</code>) "
            "e mande a foto se tiver avaria. Eu investigo, resolvo e te aviso aqui.\n\n"
            "Para a demo: <code>/demo 1</code> a <code>/demo 5</code> dispara um caso de exemplo.")


def telegram_chat_id_dica(chat_id: int | str) -> str:
    return (f"\n\n⚙️ <b>Configuração</b>: o id deste chat é <code>{html.escape(str(chat_id))}</code>. "
            "Para receber aqui os pedidos de aprovação, coloque em <code>TELEGRAM_APPROVERS_CHAT_ID</code> no .env.")


def telegram_aprovacao_texto(data: dict, case_id: str | None = None) -> str:
    e = html.escape
    linhas = [f"🔔 <b>Aprovação pedida</b> · caso <code>{e(str(case_id or data.get('case_id') or '?'))}</code>",
              f"<b>{e(str(data.get('titulo') or 'Ação do Goxy'))}</b>"]
    if data.get("resumo"):
        linhas.append(e(str(data["resumo"]))[:900])
    linhas.append("")
    linhas.append(f"Nível: <b>{e(NIVEL_ROTULO.get(str(data.get('nivel')), str(data.get('nivel') or '?')))}</b>")
    if data.get("acao_proposta"):
        linhas.append(f"Ação proposta: {e(str(data['acao_proposta']))}")
    if data.get("valor") not in (None, ""):
        linhas.append(f"Valor: <b>{brl(data['valor'])}</b>")
    ev = _evidencias(data)
    if ev:
        linhas.append("")
        linhas.append("<b>Evidências</b>")
        linhas += [f"• {e(x)}" for x in ev]
    if data.get("expira_em"):
        linhas.append("")
        linhas.append(f"⏱ Responda até {e(_expira(data))} (depois sobe ao supervisor)")
    return "\n".join(linhas)


def telegram_aprovacao_botoes(aprovacao_id: str) -> list[list[tuple[str, str]]]:
    """Linhas de botões [(rótulo, callback_data)]; callback_data ≤ 64 bytes."""
    return [[("✅ Aprovar", f"gx|aprovar|{aprovacao_id}"),
             ("⛔ Negar", f"gx|negar|{aprovacao_id}"),
             ("✏️ Ajustar", f"gx|ajustar|{aprovacao_id}")]]


def parse_callback(data: str | None) -> tuple[str, str] | None:
    partes = (data or "").split("|", 2)
    if len(partes) == 3 and partes[0] == "gx" and partes[1] in ACOES:
        return partes[1], partes[2]
    return None


def _ajuste_txt(ajuste: dict | None) -> str:
    if not ajuste:
        return ""
    p = []
    if ajuste.get("valor") not in (None, ""):
        p.append(f"novo valor {brl(ajuste['valor'])}")
    if ajuste.get("acao"):
        p.append(f"ação {ajuste['acao']}")
    if ajuste.get("nota"):
        p.append(f"nota: {ajuste['nota']}")
    return "; ".join(p)


def decisao_resumo(decidido: dict) -> str:
    rot = DECISAO_ROTULO.get(str(decidido.get("decisao")), str(decidido.get("decisao") or "decidido"))
    quem = decidido.get("aprovador") or "alguém"
    canal = decidido.get("canal") or "?"
    txt = f"{rot} por {quem} via {canal}"
    t = decidido.get("tempo_resposta_s")
    if t not in (None, ""):
        try:
            txt += f" em {int(float(t))} s"
        except (TypeError, ValueError):
            pass
    aj = _ajuste_txt(decidido.get("ajuste"))
    if aj:
        txt += f" ({aj})"
    return txt


def telegram_decisao_texto(pedido: dict, decidido: dict, case_id: str | None = None) -> str:
    base = telegram_aprovacao_texto(pedido, case_id) if pedido else f"Aprovação <code>{html.escape(str(decidido.get('aprovacao_id')))}</code>"
    base = base.split("\n⏱")[0].rstrip()
    return base + "\n\n<b>" + html.escape(decisao_resumo(decidido)) + "</b>"


def telegram_escalado_texto(pedido: dict, case_id: str | None = None) -> str:
    base = telegram_aprovacao_texto(pedido, case_id).split("\n⏱")[0].rstrip()
    return base + "\n\n⏫ <b>Sem resposta no SLA — escalado ao supervisor</b>"


def telegram_ajuste_prompt(pedido: dict) -> str:
    v = pedido.get("valor")
    atual = f" (atual: {brl(v)})" if v not in (None, "") else ""
    return (f"✏️ Responda a esta mensagem com o novo valor{atual} e, se quiser, uma nota.\n"
            "Ex.: <code>350 reembolso parcial, cliente fica com o produto</code>")


# ------------------------------------------------------------------ Slack (Block Kit)

def _mrkdwn(t: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": t[:2900]}}


def _esc(t: Any) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_aprovacao_blocks(data: dict, case_id: str | None = None, painel_url: str | None = None) -> list[dict]:
    aid = str(data.get("aprovacao_id") or "")
    cid = case_id or data.get("case_id") or "?"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🔔 {str(data.get('titulo') or 'Aprovação pedida')[:140]}"}},
    ]
    if data.get("resumo"):
        blocks.append(_mrkdwn(_esc(data["resumo"])))
    campos = [
        {"type": "mrkdwn", "text": f"*Nível*\n{_esc(NIVEL_ROTULO.get(str(data.get('nivel')), data.get('nivel') or '?'))}"},
        {"type": "mrkdwn", "text": f"*Valor*\n{brl(data.get('valor'))}"},
        {"type": "mrkdwn", "text": f"*Ação proposta*\n{_esc(data.get('acao_proposta') or '—')}"},
        {"type": "mrkdwn", "text": f"*Caso*\n`{_esc(cid)}`"},
    ]
    blocks.append({"type": "section", "fields": campos})
    ev = _evidencias(data)
    if ev:
        blocks.append(_mrkdwn("*Evidências*\n" + "\n".join(f"• {_esc(x)}" for x in ev)))
    blocks.append({"type": "actions", "block_id": f"gx_aprov|{aid}", "elements": [
        {"type": "button", "action_id": "gx_aprovar", "style": "primary", "value": aid,
         "text": {"type": "plain_text", "text": "Aprovar"}},
        {"type": "button", "action_id": "gx_negar", "style": "danger", "value": aid,
         "text": {"type": "plain_text", "text": "Negar"},
         "confirm": {"title": {"type": "plain_text", "text": "Negar a ação?"},
                     "text": {"type": "mrkdwn", "text": "O cliente recebe a resposta de recusa."},
                     "confirm": {"type": "plain_text", "text": "Negar"},
                     "deny": {"type": "plain_text", "text": "Voltar"}}},
        {"type": "button", "action_id": "gx_ajustar", "value": aid,
         "text": {"type": "plain_text", "text": "Ajustar"}},
    ]})
    ctx = []
    if data.get("expira_em"):
        ctx.append(f"⏱ responda até {_esc(_expira(data))}; depois sobe ao supervisor")
    if painel_url:
        ctx.append(f"<{painel_url}|abrir no painel>")
    ctx.append(f"aprovação `{_esc(aid)}`")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(ctx)}]})
    return blocks


def _sem_botoes(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if b.get("type") != "actions"]


def slack_decisao_blocks(pedido: dict, decidido: dict, case_id: str | None = None) -> list[dict]:
    base = _sem_botoes(slack_aprovacao_blocks(pedido or {"aprovacao_id": decidido.get("aprovacao_id")}, case_id))
    return base + [_mrkdwn(f"*{_esc(decisao_resumo(decidido))}*")]


def slack_escalado_blocks(pedido: dict, case_id: str | None = None) -> list[dict]:
    # mantém os botões: o supervisor ainda pode decidir pela mesma mensagem
    blocks = slack_aprovacao_blocks(pedido, case_id)
    blocks.insert(1, _mrkdwn(":arrow_double_up: *Sem resposta no SLA — escalado ao supervisor.*"))
    return blocks


def slack_texto_fallback(data: dict) -> str:
    return f"Aprovação pedida: {data.get('titulo') or ''} ({brl(data.get('valor'))}, nível {data.get('nivel')})"


def slack_ajuste_modal(aprovacao_id: str, pedido: dict | None = None, meta: str = "") -> dict:
    pedido = pedido or {}
    valor_inicial = pedido.get("valor")
    el_valor: dict = {"type": "plain_text_input", "action_id": "valor",
                      "placeholder": {"type": "plain_text", "text": "ex.: 350,00"}}
    if valor_inicial not in (None, ""):
        el_valor["initial_value"] = f"{float(valor_inicial):.2f}".replace(".", ",")
    return {
        "type": "modal",
        "callback_id": "gx_ajuste",
        "private_metadata": meta or aprovacao_id,
        "title": {"type": "plain_text", "text": "Ajustar decisão"},
        "submit": {"type": "plain_text", "text": "Enviar ajuste"},
        "close": {"type": "plain_text", "text": "Cancelar"},
        "blocks": [
            _mrkdwn(f"*{_esc(pedido.get('titulo') or 'Aprovação')}*\nProposta: {_esc(pedido.get('acao_proposta') or '—')} · {brl(valor_inicial)}"),
            {"type": "input", "block_id": "valor", "optional": True, "element": el_valor,
             "label": {"type": "plain_text", "text": "Novo valor (R$)"}},
            {"type": "input", "block_id": "nota", "optional": True,
             "element": {"type": "plain_text_input", "action_id": "nota", "multiline": True},
             "label": {"type": "plain_text", "text": "Nota para o Goxy"}},
        ],
    }


def slack_ajuste_de_view(values: dict) -> dict:
    """Extrai {valor?, nota?} do state.values do modal."""
    v = ((values.get("valor") or {}).get("valor") or {}).get("value") or ""
    n = ((values.get("nota") or {}).get("nota") or {}).get("value") or ""
    out = parse_ajuste(v) if v else {}
    out.pop("nota", None)
    if n.strip():
        out["nota"] = n.strip()
    return out


def slack_nivel_b_blocks(case_id: str, data: dict, minutos: int = 15) -> list[dict]:
    acao = data.get("acao") or data.get("acao_proposta") or "ação"
    return [
        _mrkdwn(f":information_source: *Executado com aviso (nível B)* · caso `{_esc(case_id)}`\n"
                f"{_esc(acao)} · {brl(data.get('valor'))}\n_{_esc(data.get('motivo') or '')}_"),
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "gx_desfazer", "style": "danger", "value": case_id,
             "text": {"type": "plain_text", "text": f"Desfazer ({minutos} min)"}}]},
    ]
