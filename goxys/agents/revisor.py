"""Revisor maker-checker (SPEC §14 ideia 6): um segundo olhar antes de pausar (C) ou congelar (D).

Determinístico: confere a decisão contra os dados do caso e a política. Pode rebaixar uma negativa sem
evidência objetiva para aprovação humana (o agente nunca nega sozinho por suspeita).
"""
from __future__ import annotations

from core.state import Case

ACOES_POR_INTENCAO = {
    "avaria": {"reenvio", "reembolso", "solicitar_foto", "negar"},
    "item_faltante": {"reenvio", "reembolso", "solicitar_foto", "negar"},
    "erro_personalizacao": {"nova_producao", "reembolso", "solicitar_foto", "negar"},
    "atraso": {"chamado_transportadora", "reenvio", "reembolso", "informar_status"},
    "rastreio": {"chamado_transportadora", "informar_status", "reenvio", "reembolso"},
    "reembolso": {"reembolso", "negar"},
    "cobranca_duplicada": {"reembolso"},
    "cancelamento": {"reembolso", "negar"},
    "troca": {"troca", "negar"},
    "reacao_adversa": {"cosmetovigilancia"},
}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def revisar(case: Case) -> dict:
    dec = case.decisao
    d = case.dossie or {}
    v = case.veracidade or {}
    t = case.triagem or {}
    pedido, hist = d.get("pedido") or {}, d.get("historico") or {}
    checagens: list[dict] = []
    ressalvas: list[str] = []

    def chk(item: str, ok: bool, ressalva: str | None = None):
        checagens.append({"item": item, "ok": ok})
        if not ok and ressalva:
            ressalvas.append(ressalva)

    acao, valor, nivel = dec.get("acao"), _f(dec.get("valor")), dec.get("nivel")
    chk("decisão tem justificativa e versão da política", bool(dec.get("motivo") and dec.get("politica_versao")),
        "decisão sem justificativa/versão")
    if valor:
        chk("valor proposto ≤ valor pago no pedido", valor <= _f(pedido.get("valor"), valor) + 0.01,
            f"valor proposto acima do pago ({pedido.get('valor')})")
    oid = case.order_id
    estornos = [e for e in (hist.get("estornos") or []) if e.get("order_id") == oid]
    chk("sem estorno anterior neste pedido", not estornos, f"já existe estorno neste pedido ({len(estornos)})")
    trocas = [x for x in (hist.get("trocas") or []) if oid and oid in (x.get("order_id_original"), x.get("order_id"))]
    chk("sem troca/reenvio anterior neste pedido", not trocas, f"já houve {len(trocas)} troca/reenvio neste pedido")
    permitidas = ACOES_POR_INTENCAO.get(t.get("intencao"))
    if permitidas is not None and acao != "humano":
        chk(f"ação '{acao}' permitida para '{t.get('intencao')}' (politicas)", acao in permitidas,
            f"ação {acao} fora das permitidas para {t.get('intencao')}")
    chk("triagem com confiança suficiente", _f(t.get("confianca"), 1) >= 0.6,
        f"triagem com confiança baixa ({t.get('confianca')})")
    inc = [i for i in d.get("inconsistencias") or [] if i.get("tipo") == "inconsistencia"]
    chk("dados consistentes entre sistemas", not inc, "; ".join(i["titulo"] for i in inc) if inc else None)

    rebaixar = None
    if acao == "negar":
        objetivas = [e for e in v.get("evidencias") or [] if e.get("tipo") in ("reuso", "exif") and e.get("peso", 0) <= -20]
        ok = bool(objetivas)
        chk("negativa apoiada em evidência objetiva (metadados/reuso)", ok,
            "negativa sem evidência objetiva: rebaixar para aprovação humana")
        if not ok:
            rebaixar = "C"
    if nivel == "D" and acao != "negar":
        chk("gatilho de humano presente (jurídico/imprensa/menor)",
            bool(t.get("risco_juridico") or t.get("imprensa") or t.get("menor_de_idade")))
    escritas = [a for a in case.acoes if a.tipo in ("escrita", "escrita_critica") and a.ferramenta != "open_internal_ticket"]
    chk("nenhuma escrita executada antes da revisão", not escritas, "houve escrita antes da revisão")

    aprovado = rebaixar is None
    parecer = ("confere: " if aprovado else "não confere: ") + (
        f"{sum(c['ok'] for c in checagens)}/{len(checagens)} checagens ok" + (f"; {len(ressalvas)} ressalva(s)" if ressalvas else ""))
    return {"aprovado": aprovado, "ressalvas": ressalvas, "checagens": checagens, "parecer": parecer,
            "rebaixar_para": rebaixar}
