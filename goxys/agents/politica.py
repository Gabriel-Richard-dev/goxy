"""Política de autonomia (SPEC §6) — `decide(case)` → nível A..D, determinístico e auditável.

Combina: valor envolvido, score de veracidade do Detetive, risco do cliente, ameaça jurídica/imprensa,
irreversibilidade da ação e o custo de erro assimétrico (SPEC §14 ideia 2). Os limites vêm de
`policy/autonomia.yaml` (versionado); toda decisão leva `politica_versao`.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from core import config
from core.state import Case

from .base import agora_ambiente, brl, parse_data


@lru_cache(maxsize=4)
def _carregar(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def carregar() -> dict:
    p = config.POLICY_PATH
    return _carregar(str(p), p.stat().st_mtime)


def versao() -> str:
    return str(carregar().get("versao", "?"))


# Políticas propostas (aprendizado), em memória: só valem para casos marcados com `case.grafo.politica_override`.
# É assim que a validação roda os clientes sintéticos com a política nova sem tocar no YAML nem nos outros casos.
OVERRIDES: dict[str, dict] = {}


def politica_para(case: Case) -> dict:
    oid = (getattr(case, "grafo", None) or {}).get("politica_override")
    return OVERRIDES.get(oid) or carregar()


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ proposta de ação

SEM_PEDIDO_OK = {"duvida", "elogio", "outros"}     # intenções que se resolvem sem um pedido


def propor_acao(case: Case) -> dict:
    """Qual ação a regra de negócio pede para este caso (antes de decidir a autonomia)."""
    t = case.triagem or {}
    intencao = t.get("intencao", "outros")
    pedido = (case.dossie or {}).get("pedido") or {}
    rastreio = (case.dossie or {}).get("rastreio") or {}
    valor = _f(pedido.get("valor"))
    sku = pedido.get("sku")
    pede_reembolso = bool(t.get("pede_reembolso"))
    tem_foto = bool(case.anexos)

    def r(acao, valor=0.0, motivo="", tipo=None, **extra):
        return {"acao": acao, "valor": round(valor, 2), "sku": extra.pop("sku", sku), "tipo_troca": tipo,
                "motivo": motivo, **extra}

    pol = carregar()
    # Nunca inventar dado: sem pedido válido, nenhuma ação sobre o pedido (nem "informar status").
    if intencao not in SEM_PEDIDO_OK and not pedido.get("order_id"):
        nf = (case.dossie or {}).get("pedido_nao_encontrado")
        if nf:
            return r("pedir_dados", 0, f"Pedido {nf.get('numero_informado')} não encontrado no ERP: pedir o número "
                                       "certo ao cliente (nunca supor status)")
        return r("pedir_dados", 0, "Pedido não informado: pedir o número ao cliente antes de qualquer ação")
    if intencao == "reacao_adversa":
        return r("cosmetovigilancia", 0, "reação adversa: orientar suspensão do uso e registrar cosmetovigilância "
                                         "em até 24 h (trocar o produto não substitui o registro)")
    if intencao == "cobranca_duplicada":
        capt = capturas(pedido)
        if len(capt) >= 2:
            excesso = sum(_f(p.get("valor")) for p in capt) - valor
            dup = excesso if 0 < excesso <= valor + 0.01 else min(_f(p.get("valor")) for p in capt)
            return r("reembolso", dup, f"{len(capt)} capturas no gateway: estorno da duplicidade", direito="cobranca_dobro")
        return r("reembolso", valor, "cliente relata cobrança em dobro, gateway não confirma")
    if intencao == "cancelamento":
        ent = parse_data(pedido.get("data_entrega"))
        lim = int(pol.get("direitos_cliente", {}).get("arrependimento_dias_corridos", 7))
        st = (pedido.get("status_entrega") or "").lower()
        if st in ("aguardando_separacao",) or (ent and (agora_ambiente() - ent).days <= lim):
            return r("reembolso", valor, f"arrependimento dentro de {lim} dias (CDC art. 49)", direito="arrependimento")
        return r("reembolso", valor, f"arrependimento fora do prazo de {lim} dias", fora_prazo_direito=True)

    if intencao in ("avaria", "item_faltante", "erro_personalizacao"):
        if not tem_foto:
            return r("solicitar_foto", 0, "política exige foto para avaria/faltante/personalização")
        item = item_afetado(case, pedido, pol) if intencao == "item_faltante" else None
        v_item = item["valor"] if item else valor
        if pede_reembolso:
            return r("reembolso", v_item, f"{intencao}: cliente pede o dinheiro de volta",
                     **({"sku": item["sku"], "item": item} if item else {}))
        tipo = "nova_producao" if intencao == "erro_personalizacao" else "reenvio"
        return r(tipo, v_item, f"{intencao}: {tipo} sem custo mediante foto"
                 + (f" (item: {item['descricao']})" if item else ""), tipo, **({"sku": item["sku"], "item": item} if item else {}))
    if intencao in ("atraso", "rastreio"):
        st = (rastreio.get("status") or pedido.get("status_entrega") or "").lower()
        if "extravi" in st:
            return r("reembolso" if pede_reembolso else "reenvio", valor, "extravio confirmado",
                     None if pede_reembolso else "reenvio")
        corridos = int(_f(rastreio.get("dias_atraso")))
        dias = int(_f(rastreio.get("dias_uteis_atraso"), corridos))
        if rastreio.get("atrasado") or corridos > 0:
            lim = int(carregar().get("prazos", {}).get("atraso_dias_uteis_so_chamado", 7))
            if dias > lim:
                return r("reembolso" if pede_reembolso else "reenvio", valor,
                         f"atraso de {dias} dias úteis (> {lim}): reenvio ou reembolso",
                         None if pede_reembolso else "reenvio")
            return r("chamado_transportadora", 0, f"atraso de {corridos} dia(s) corrido(s), {dias} útil(eis) "
                                                  f"(≤ {lim} úteis): chamado na transportadora")
        return r("informar_status", 0, "dentro do prazo: informar status e previsão")
    if intencao == "reembolso":
        return r("reembolso", valor, "cliente pede estorno")
    if intencao == "troca":
        return r("troca", valor, "troca de produto", "troca")
    return r("responder", 0, "dúvida/elogio/outros: só responder")


def capturas(pedido: dict) -> list[dict]:
    """Cobranças efetivamente capturadas no gateway (prova objetiva de cobrança em dobro quando ≥ 2)."""
    return [p for p in (pedido.get("pagamentos") or [])
            if str(p.get("status", "")).lower() in ("capturada", "paga", "aprovada", "aprovado")]


def item_afetado(case: Case, pedido: dict, pol: dict) -> dict | None:
    """Item que faltou: brinde de campanha (preço 0 → custo estimado) ou o item citado no texto."""
    itens = pedido.get("itens") or []
    if not itens:
        return None
    txt = (case.mensagem or "").lower()
    def eh_brinde(i):
        return "brinde" in str(i.get("tipo_item") or "").lower() or "brinde" in str(i.get("descricao") or "").lower() \
            or _f(i.get("preco_unitario")) == 0
    alvo = None
    if re.search(r"brinde|mimo|necessaire|presente da campanha", txt):
        alvo = next((i for i in itens if eh_brinde(i)), None)
    if alvo is None:
        palavras = set(re.findall(r"[a-zà-ú]{4,}", txt))
        melhor = max(itens, key=lambda i: len(palavras & set(re.findall(r"[a-zà-ú]{4,}", str(i.get("descricao") or "").lower()))))
        if palavras & set(re.findall(r"[a-zà-ú]{4,}", str(melhor.get("descricao") or "").lower())):
            alvo = melhor
    if alvo is None:
        return None
    v = _f(alvo.get("valor_total")) or _f(alvo.get("preco_unitario")) * max(1, int(_f(alvo.get("quantidade"), 1)))
    estimado = False
    if v <= 0:
        v, estimado = _f(pol.get("valor_item", {}).get("custo_brinde_estimado_rs"), 25), True
    return {"sku": alvo.get("sku"), "descricao": alvo.get("descricao"), "valor": round(v, 2),
            "brinde": eh_brinde(alvo), "valor_estimado": estimado}


# ------------------------------------------------------------------ decisão

def _risco_cliente(case: Case, pol: dict) -> tuple[bool, list[str]]:
    cli = (case.dossie or {}).get("cliente") or {}
    lim = pol.get("risco_cliente", {})
    motivos = []
    if cli.get("flag_risco") in (True, 1, "1", "true", "sim"):
        motivos.append("cliente com flag de risco")
    if _f(cli.get("qtd_reclamacoes_90d")) >= _f(lim.get("qtd_reclamacoes_90d_max", 3)):
        motivos.append(f"{int(_f(cli.get('qtd_reclamacoes_90d')))} reclamações em 90 dias")
    if _f(cli.get("n_trocas_12m")) >= _f(lim.get("trocas_12m_max", 3)):
        motivos.append(f"{int(_f(cli.get('n_trocas_12m')))} trocas em 12 meses")
    return bool(motivos), motivos


def custo_assimetrico(case: Case, valor: float, score: float | None, pol: dict) -> dict:
    """Custos esperados de cada erro, em R$ (parametros.md). Explícito nos sinais da decisão."""
    ce = pol.get("custo_erro", {})
    cli = (case.dossie or {}).get("cliente") or {}
    s = 0.5 if score is None else max(0.0, min(1.0, score / 100))
    p_fraude, p_verdadeira = 1 - s, s
    receita = _f(cli.get("receita_acumulada"), valor)
    recompra = ce.get("recompra_ano_acima_1500", 0.7) if receita > ce.get("limite_receita_fiel_rs", 1500) \
        else ce.get("recompra_ano", 0.28)
    ltv = max(receita, valor) * (1 + recompra)
    ra = ce.get("chance_virar_reclame_aqui", 0.3) * ce.get("custo_reclame_aqui_rs", 42)
    pagar_indevido = valor * ce.get("pagar_indevido_pct_valor", 0.45) * p_fraude
    negar_verdadeira = p_verdadeira * (ce.get("negar_verdadeira_ltv_fator", 0.5) * ltv + ra)
    sinalizar = ce.get("custo_sinalizar_supervisor_rs", 6) + p_verdadeira * ra * 0.5
    return {
        "p_fraude": round(p_fraude, 2),
        "ltv_estimado_rs": round(ltv, 2),
        "esperado_pagar_indevido_rs": round(pagar_indevido, 2),
        "esperado_negar_verdadeira_rs": round(negar_verdadeira, 2),
        "esperado_sinalizar_supervisor_rs": round(sinalizar, 2),
        "leitura": (f"pagar indevido ≈ {brl(pagar_indevido)} · negar um honesto ≈ {brl(negar_verdadeira)} · "
                    f"sinalizar ao supervisor ≈ {brl(sinalizar)}"),
    }


def faixa(score: float | None, pol: dict) -> str:
    if score is None:
        return "sem_score"
    fx = pol.get("custo_erro", {}).get("faixas", {})
    if score >= fx.get("verde", 70):
        return "verde"
    if score >= fx.get("amarela", 40):
        return "amarela"
    return "vermelha"


def decide(case: Case) -> dict:
    d = _decide(case)
    d.setdefault("regra", regra_de(d["motivo"]))
    d.setdefault("porque", porque(case, d))
    return d


def _decide(case: Case) -> dict:
    pol = politica_para(case)
    niveis = pol["niveis"]
    prop = propor_acao(case)
    acao = prop["acao"]
    acfg = pol.get("acoes", {}).get(acao, {})
    valor = _f(prop["valor"])
    t = case.triagem or {}
    v = case.veracidade or {}
    score = v.get("score")
    score = None if score is None else float(score)
    risco, risco_motivos = _risco_cliente(case, pol)
    custos = custo_assimetrico(case, valor, score, pol)
    gat = pol.get("gatilhos_d", {})
    limite_papel = _f(acfg.get("limite_papel_rs"), 0)
    pedido = (case.dossie or {}).get("pedido") or {}

    # prazo de 7 dias corridos do recebimento (avaria / faltante / personalização)
    fora_prazo = False
    if acao in ("reenvio", "nova_producao", "reembolso") and t.get("intencao") in ("avaria", "item_faltante",
                                                                                   "erro_personalizacao"):
        ent = parse_data(pedido.get("data_entrega"))
        lim = int(pol.get("prazos", {}).get("avaria_dias_corridos_do_recebimento", 7))
        if ent and (agora_ambiente() - ent).days > lim:
            fora_prazo = True

    sinais = {
        "acao": acao,
        "valor_rs": valor,
        "limite_papel_rs": limite_papel,
        "irreversivel": bool(acfg.get("irreversivel")),
        "score_veracidade": score,
        "faixa": faixa(score, pol),
        "fraude_objetiva": bool(v.get("fraude_objetiva")),
        "cliente_com_alerta": risco,
        "risco_cliente": risco_motivos,
        "risco_juridico": bool(t.get("risco_juridico")),
        "imprensa": bool(t.get("imprensa")),
        "menor_de_idade": bool(t.get("menor_de_idade")),
        "injecao_no_texto": bool(t.get("injecao")),
        "direito_cliente": prop.get("direito"),
        "item_afetado": prop.get("item"),
        "canal_publico": case.canal == "reclame_aqui",
        "fora_do_prazo_7d": fora_prazo,
        "confianca_triagem": t.get("confianca"),
        "custo_erro": custos,
    }

    def out(nivel: str, motivo: str, acao_final: str | None = None) -> dict:
        return {"nivel": nivel, "acao": acao_final or acao, "valor": valor, "sku": prop.get("sku"),
                "tipo_troca": prop.get("tipo_troca"), "motivo": motivo, "motivo_acao": prop.get("motivo"),
                "sinais": sinais, "politica_versao": str(pol.get("versao")),
                "comportamento": niveis.get(nivel, {}).get("comportamento", "")}

    # 1) Gatilhos de humano: jurídico, imprensa, menor
    gatilhos = [n for n, k in (("ameaça jurídica", "risco_juridico"), ("imprensa/exposição pública", "imprensa"),
                               ("menor de idade", "menor_de_idade")) if gat.get(k) and sinais[k]]
    if gatilhos:
        d = out("D", f"{' e '.join(gatilhos).capitalize()}: humano assume, ações congeladas", "humano")
        d["acao_sugerida"] = acao   # o que a regra pediria; fica para o analista sênior decidir
        return d

    # 1b) Sem pedido válido: pede os dados ao cliente (autônomo, não movimenta nada) e espera a resposta
    if acao == "pedir_dados":
        return out("A", prop["motivo"])

    # 2) Fraude com evidência objetiva (foto reaproveitada, editor/IA) e score vermelho
    if score is not None and score < _f(gat.get("fraude_objetiva_score_max"), 40) and sinais["fraude_objetiva"]:
        return out("D", f"Veracidade {score:.0f}/100 com evidência objetiva de fraude: reclamação não procede; "
                        "ações congeladas e supervisor revisa (nunca acusar o cliente)", "negar")

    d = _decidir_resto(case, pol, niveis, prop, acao, acfg, valor, score, risco, risco_motivos, custos,
                       limite_papel, fora_prazo, sinais, out)
    # Texto do cliente é dado, nunca instrução: tentativa de mandar no agente nunca fica em A/B
    if sinais["injecao_no_texto"] and d["nivel"] in ("A", "B") and acao not in ("responder", "informar_status",
                                                                                  "pedir_dados", "solicitar_foto"):
        minimo = str(pol.get("injecao_minimo_nivel", "C"))
        d = out(minimo, "Texto do cliente tenta dar ordens ao agente (ignorado como instrução): "
                        f"aprovação obrigatória. Antes: {d['motivo']}")
    d["regra"] = regra_de(d["motivo"])
    d["porque"] = porque(case, d)
    return d


def _decidir_resto(case, pol, niveis, prop, acao, acfg, valor, score, risco, risco_motivos, custos,
                   limite_papel, fora_prazo, sinais, out) -> dict:
    # 3a) Ações que exigem o supervisor (cosmetovigilância)
    if acfg.get("exige_supervisor"):
        return out("C", f"{acao.capitalize()}: registro obrigatório em até {acfg.get('prazo_horas', 24)} h pelo "
                        "supervisor/responsável técnica; o agente já orienta o cliente e prepara o registro")
    # 3) Ações sem valor (chamado, status, resposta, pedir foto)
    if acfg.get("sem_valor"):
        return out("A", f"{acao.replace('_', ' ')} não movimenta dinheiro nem estoque: autônomo")

    # 3b) Direitos do cliente que não dependem de veracidade (CDC art. 49; cobrança em dobro comprovada)
    if prop.get("direito"):
        nome = {"arrependimento": "Arrependimento dentro de 7 dias (CDC art. 49)",
                "cobranca_dobro": "Cobrança em dobro comprovada no gateway"}.get(prop["direito"], prop["direito"])
        if limite_papel and valor > limite_papel:
            return out("C", f"{nome}, mas {brl(valor)} acima da alçada do atendente ({brl(limite_papel)}): aprovação")
        return out("A", f"{nome}: direito do cliente, não depende de nota; {brl(valor)} dentro da alçada")
    if prop.get("fora_prazo_direito"):
        return out("C", "Arrependimento fora do prazo de 7 dias: exceção só com aprovação")

    # 4) Score vermelho sem evidência objetiva: nunca nega sozinho → supervisor
    if score is not None and score < _f(niveis["C"].get("veracidade_min"), 40):
        return out("C", f"Veracidade {score:.0f}/100 baixa, mas sem evidência objetiva: supervisor decide "
                        "(o agente nunca nega sozinho por suspeita)")

    # 5) Valor e irreversibilidade
    if valor > _f(niveis["B"].get("valor_max_rs"), 500):
        return out("C", f"Valor {brl(valor)} acima de {brl(niveis['B']['valor_max_rs'])}: aprovação obrigatória")
    if limite_papel and valor > limite_papel:
        irr = " e irreversível" if acfg.get("irreversivel") else ""
        return out("C", f"{acao.replace('_', ' ').capitalize()} de {brl(valor)}{irr} acima da alçada do atendente "
                        f"({brl(limite_papel)}): aprovação obrigatória")
    if fora_prazo:
        return out("C", "Fora do prazo de 7 dias do recebimento: exceção à política só com aprovação")

    # 6) Faixa amarela: custo de erro assimétrico decide entre agir e avisar (B) ou pedir aprovação (C)
    if score is None or score < _f(niveis["B"].get("veracidade_min"), 70):
        ce = pol.get("custo_erro", {})
        if (score is not None and valor <= _f(ce.get("faixa_cinzenta_valor_max_rs"), 200) and not risco
                and custos["esperado_pagar_indevido_rs"] < custos["esperado_sinalizar_supervisor_rs"]):
            return out("B", f"Faixa amarela ({score:.0f}/100), mas pagar indevido ({brl(custos['esperado_pagar_indevido_rs'])}) "
                            f"custa menos que travar o cliente ({brl(custos['esperado_sinalizar_supervisor_rs'])}): "
                            "age e avisa o supervisor (custo assimétrico)")
        s_txt = "sem score" if score is None else f"{score:.0f}/100"
        return out("C", f"Veracidade {s_txt} na faixa cinzenta (40–70): aprovação obrigatória")

    # 7) A: barato, verdade comprovada, cliente limpo
    a = niveis["A"]
    if valor <= _f(a.get("valor_max_rs"), 150) and score >= _f(a.get("veracidade_min"), 80) and not risco:
        return out("A", f"Valor {brl(valor)} ≤ {brl(a['valor_max_rs'])}, veracidade {score:.0f}/100 e cliente sem "
                        "alerta: resolve sozinho")
    # 8) B
    b = niveis["B"]
    motivo_b = f"Valor {brl(valor)} e veracidade {score:.0f}/100"
    if risco:
        motivo_b += f"; cliente com alerta ({', '.join(risco_motivos)})"
    return out("B", motivo_b + ": age e avisa o supervisor, com desfazer em "
                    f"{b.get('desfazer_em_min', 15)} min")


_REGRAS = [  # motivo (template fixo desta política) → regra
    ("pedir o número", "pedir_dados"),
    ("humano assume", "gatilho_d"), ("evidência objetiva de fraude", "fraude"), ("Texto do cliente tenta", "injecao"),
    ("registro obrigatório", "supervisor_obrigatorio"), ("não movimenta dinheiro", "sem_valor"),
    ("Arrependimento fora", "direito_fora_prazo"), ("direito do cliente", "direito"), ("sem evidência objetiva", "suspeita_sem_prova"),
    ("acima da alçada do atendente (", "acima_alcada"), ("aprovação obrigatória", "valor_ou_cinza"),
    ("Fora do prazo de 7 dias", "fora_prazo"), ("custo assimétrico", "assimetrico"), ("resolve sozinho", "a"),
    ("desfazer em", "b"),
]


def regra_de(motivo: str) -> str:
    return next((r for k, r in _REGRAS if k in (motivo or "")), "outra")


def porque(case: Case, d: dict) -> str:
    """UMA frase curta e humana (≤ 140), sem jargão, a partir dos sinais. Determinística; a IA só pode polir."""
    sg, v, t = d.get("sinais") or {}, case.veracidade or {}, case.triagem or {}
    acao, valor, regra = d.get("acao"), d.get("valor") or 0, regra_de(d.get("motivo", ""))
    evid = v.get("evidencias") or []
    tem = lambda tipo, txt="": any(e.get("tipo") == tipo and txt in e.get("titulo", "") for e in evid)  # noqa: E731
    nomes = {"reenvio": "Reenvio", "reembolso": "Reembolso", "troca": "Troca", "nova_producao": "Nova produção"}
    oque = f"{nomes.get(acao, (acao or '').replace('_', ' ').capitalize())} de {brl(valor)}"
    if regra == "pedir_dados":
        nf = (case.dossie or {}).get("pedido_nao_encontrado")
        f = (f"O pedido {nf['numero_informado']} não existe no sistema; antes de responder, peço o número certo ao cliente."
             if nf else "O cliente não informou o pedido; peço o número antes de qualquer resposta.")
    elif regra == "gatilho_d":
        q = "ameaçou processo" if sg.get("risco_juridico") else "ameaçou expor a marca"
        f = f"O cliente {q}: uma pessoa do time assume e o Goxy não age sozinho."
    elif regra == "fraude":
        partes = ["a foto já apareceu em outro pedido"] if tem("reuso") else []
        if tem("exif", "editada"):
            partes.append("foi editada")
        elif tem("exif", "antes da entrega"):
            partes.append("foi tirada antes da entrega")
        f = (" e ".join(partes) or "a evidência não confere").capitalize() + ": a reclamação não procede e o supervisor revisa."
    elif regra == "injecao":
        f = "A mensagem tenta dar ordens ao Goxy; por segurança, uma pessoa aprova antes de qualquer ação."
    elif regra == "supervisor_obrigatorio":
        f = "Reação a cosmético exige registro em 24 h e o olhar de um supervisor; o cliente já foi orientado."
    elif regra == "sem_valor":
        f = {"chamado_transportadora": "O pedido passou da data prometida; abrir chamado na transportadora resolve sem custo.",
             "informar_status": "O pedido está dentro do prazo: basta informar o status ao cliente.",
             "solicitar_foto": "Para avaria, a política pede foto antes de reenviar.",
             }.get(acao, "Não envolve dinheiro nem estoque: o Goxy responde sozinho.")
    elif regra == "direito":
        f = ("Arrependimento em até 7 dias é direito do cliente e o valor cabe no que o Goxy resolve sozinho."
             if sg.get("direito_cliente") == "arrependimento" else
             "O gateway mostra duas cobranças: estornar a duplicada é direito do cliente.")
        if d.get("nivel") == "C":
            f = f.split(" e o valor")[0].rstrip(".") + ", mas o valor passa do limite do Goxy: um supervisor aprova."
    elif regra == "direito_fora_prazo":
        f = "O arrependimento passou dos 7 dias: só um supervisor pode abrir exceção."
    elif regra == "suspeita_sem_prova":
        f = "A evidência é fraca, mas sem prova objetiva o Goxy nunca nega sozinho: um supervisor decide."
    elif regra in ("acima_alcada", "valor_ou_cinza") and valor and (valor > (sg.get("limite_papel_rs") or 0) or valor > 500):
        f = f"{oque} passa do limite do Goxy"
        if sg.get("faixa") in ("amarela", "vermelha"):
            f += " e a foto não mostra o dano com clareza" if case.anexos else " e a evidência é fraca"
        f += ": um supervisor aprova."
    elif regra == "valor_ou_cinza":
        f = "A evidência é duvidosa e errar custa caro para os dois lados: um supervisor decide."
    elif regra == "fora_prazo":
        f = "A reclamação chegou depois dos 7 dias da entrega: só com aprovação de um supervisor."
    elif regra == "assimetrico":
        f = "A evidência é duvidosa, mas o valor é baixo: pagar errado custa menos que travar um cliente honesto."
    elif regra == "a":
        partes = []
        if case.anexos:
            partes.append("Foto inédita" if not tem("reuso") else "Foto conferida")
            if tem("exif", "depois da entrega"):
                partes.append("tirada depois da entrega")
        elif sg.get("score_veracidade") is not None:
            partes.append("A reclamação confere com os sistemas")
        base = ", ".join(partes) or "Tudo confere"
        f = f"{base}, e o valor está dentro do que o Goxy pode resolver sozinho."
    elif regra == "b":
        f = "A reclamação procede e o valor é médio: o Goxy resolve e avisa o supervisor, que pode desfazer."
    else:
        f = (d.get("motivo") or "").split(":")[0] + "."
    return f if len(f) <= 140 else f[:139].rstrip() + "…"


def permite_escrita(decisao: dict, aprovacao: dict | None = None) -> tuple[bool, str]:
    """O Executor pergunta antes de qualquer escrita."""
    if decisao.get("congelado"):
        return False, "ações congeladas"
    n = decisao.get("nivel")
    if n in ("A", "B"):
        return True, f"nível {n}"
    if n == "C":
        ap = aprovacao or {}
        if ap.get("decisao") in ("aprovar", "ajustar"):
            return True, f"nível C aprovado por {ap.get('aprovador') or 'humano'}"
        return False, "nível C sem aprovação"
    return False, f"nível {n} não permite escrita"


def sem_pii(txt: str) -> str:
    return re.sub(r"\d{3}\.\d{3}\.\d{3}-\d{2}", "[CPF]", txt or "")
