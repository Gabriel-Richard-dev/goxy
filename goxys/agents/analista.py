"""Analista de causa-raiz (SPEC §4, §14 ideias 3/8).

`insights()` agrupa os contatos repetidos do histórico em clusters com causa provável e propõe uma
ação preventiva com número de "contatos evitáveis". Calcula a partir dos dados reais
(`data/goxys.db` se existir; senão lê os CSVs de `data_raw/.../atendimento/` direto) e guarda em cache.

Clusters:
  1. transportadora × UF  — pedidos com várias conversas / pendentes por atraso e rastreio
  2. impressora de personalização — reclamações de impressão (torta, borrada, nome errado) por máquina
  3. lote GoBeauté — vazamento / reação adversa concentrados em um lote
  4. brinde da campanha não enviado — por CD de separação
  5. cobranças em dobro — 2 transações aprovadas para o mesmo pedido (pagamentos_gateway)
Primeiro insight: `fotos_reaproveitadas` — auditoria retroativa de anexos com hash já enviado antes por outro
cliente e o que foi pago por eles (trocas_reenvios/estornos pela conversation_id). Também em `insights()["fotos_reaproveitadas"]`.

`run(case)` marca o passo "analista" na timeline quando o caso pertence a um cluster conhecido.
"""
from __future__ import annotations

import csv
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache

try:
    from core import config
except ModuleNotFoundError:  # import como goxys.agents.analista
    from goxys.core import config  # type: ignore

CSV_DIR = config.DATA_RAW / "atendimento"

# parametros.md
CUSTO_CANAL = {"whatsapp": 4.80, "email": 7.20, "reclame_aqui": 42.00}
LIMITE_NOTIFICACOES_DIA = 300
FATOR_AMOSTRA = 4          # historico_conversas é amostra de 1 em cada 4 clientes
AGORA = config.NOW_ISO[:10]

TERMOS_IMPRESSAO = ("torta", "torto", "borrad", "mancha", "descasc", "apagad", "desbot", "errado",
                    "escreveram", "outra pess", "falhad", "pro canto", "desalinh", "impress")
TERMOS_QUALIDADE = ("vaz", "alergi", "irrit", "coceira", "vermelh", "inchad", "ardeu", "ardendo",
                    "textura", "talhad", "grumo", "estragad", "mofo", "cheiro estranho", "reação")
TERMOS_BRINDE = ("brinde", "necessaire de brinde", "mimo")
TERMOS_DOBRO = ("duas vezes", "duplicid", "em dobro", "cobraram no cartão também", "cobrado 2", "cobrança dupla")


# ---------------------------------------------------------------- leitura

def _tabela(nome: str) -> list[dict]:
    """Lê do SQLite se a tabela existir; senão do CSV. Sempre devolve valores como str."""
    if config.DB_PATH.exists():
        try:
            con = sqlite3.connect(config.DB_PATH)
            con.row_factory = sqlite3.Row
            rows = con.execute(f'SELECT * FROM "{nome}"').fetchall()
            con.close()
            if rows:
                return [{k: ("" if r[k] is None else str(r[k])) for k in r.keys()} for r in rows]
        except sqlite3.Error:
            pass
    path = CSV_DIR / f"{nome}.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _custo(convs: list[dict]) -> float:
    return round(sum(CUSTO_CANAL.get(c.get("canal", ""), 4.80) for c in convs), 2)


# ---------------------------------------------------------------- auditoria retroativa de fotos

def _fotos_reaproveitadas() -> dict:
    """Anexos reais cujo hash perceptual já tinha sido enviado ANTES por outro cliente, e quanto foi pago por eles."""
    anexos = sorted((a for a in _tabela("anexos_conversas")
                     if a.get("hash_perceptual") and not a.get("anexo_id", "").startswith("DEMO")),
                    key=lambda a: a.get("enviado_em") or "")
    pagos_conv: dict[str, float] = defaultdict(float)
    for t in _tabela("trocas_reenvios"):
        if t.get("conversation_id"):
            pagos_conv[t["conversation_id"]] += _f(t.get("custo_total"))
    for e in _tabela("estornos"):
        if e.get("conversation_id") and (e.get("status") or "").lower() not in ("negado", "recusado", "cancelado"):
            pagos_conv[e["conversation_id"]] += _f(e.get("valor"))
    campos = ("anexo_id", "customer_id", "order_id", "enviado_em", "canal")
    por_hash: dict[str, list[dict]] = defaultdict(list)
    for a in anexos:
        por_hash[a["hash_perceptual"]].append(a)
    casos, todos = [], []
    for h, grupo in por_hash.items():
        orig = grupo[0]
        reusos = [{**{k: a.get(k) for k in campos}, "pago_rs": round(pagos_conv.get(a.get("conversation_id"), 0.0), 2),
                   "conversation_id": a.get("conversation_id")}
                  for a in grupo[1:] if a.get("customer_id") != orig.get("customer_id")
                  and (a.get("enviado_em") or "") > (orig.get("enviado_em") or "")]
        if reusos:
            casos.append({"hash": h, "original": {k: orig.get(k) for k in campos}, "reusos": reusos})
            todos += reusos
    casos.sort(key=lambda c: c["original"]["enviado_em"] or "")
    pagos = [r for r in todos if r["pago_rs"] > 0]
    custo = round(sum(r["pago_rs"] for r in todos), 2)
    clientes = {r["customer_id"] for r in todos} | {c["original"]["customer_id"] for c in casos}
    return {
        "id": "fotos-reaproveitadas", "tipo": "fotos_reaproveitadas",
        "titulo": f"{len(pagos)} fotos reaproveitadas foram pagas",
        "reusos": len(todos), "hashes": len(casos), "clientes_distintos": len(clientes),
        "pagos": len(pagos), "custo_rs": custo, "casos": casos,
        "acao": f"Detetive teria barrado os {len(pagos)} antes do reenvio",
        # campos de cluster (compatível com clusters[] da tela de resultados)
        "chave": {"hashes": [c["hash"] for c in casos]},
        "tamanho": len(todos), "conversas": len(todos), "custo_estimado_rs": custo,
        "causa_provavel": (f"{len(casos)} fotos já enviadas por um cliente voltaram em {len(todos)} reclamações de "
                           "outros clientes; o atendimento não compara o hash perceptual e aprovou a troca ou reenvio."),
        "acao_preventiva": f"Detetive teria barrado os {len(pagos)} antes do reenvio (perícia com reuso de hash).",
        "contatos_evitaveis": 0, "economia_estimada_rs": custo,
    }


# ---------------------------------------------------------------- cálculo

def _calcular() -> dict:
    t0 = time.perf_counter()
    conversas = _tabela("historico_conversas")
    fila = _tabela("fila_aberta")
    pedidos = {p["order_id"]: p for p in _tabela("pedidos")}
    msgs: dict[str, list[str]] = defaultdict(list)
    for m in _tabela("historico_mensagens"):
        if m.get("autor") == "cliente":
            msgs[m["conversation_id"]].append((m.get("texto") or "").lower())
    todas = conversas + fila
    texto = {c["conversation_id"]: " ".join(msgs.get(c["conversation_id"], [])) for c in todas}
    por_pedido: dict[str, list[dict]] = defaultdict(list)
    for c in todas:
        if c.get("order_id"):
            por_pedido[c["order_id"]].append(c)

    clusters: list[dict] = []

    # 1) transportadora × UF -------------------------------------------------
    rota_convs: dict[tuple, list[dict]] = defaultdict(list)
    rota_pedidos: dict[tuple, set] = defaultdict(set)
    for oid, cs in por_pedido.items():
        p = pedidos.get(oid)
        if not p:
            continue
        logisticos = [c for c in cs if c.get("tag_agente") in ("Atraso", "Rastreio")
                      or any(k in texto[c["conversation_id"]] for k in ("rastreio", "atras", "não chegou", "nao chegou", "prazo"))]
        if not logisticos:
            continue
        repetido = len(cs) >= 2 or any(c.get("status") in ("reaberto", "pendente") for c in cs)
        if repetido:
            k = (p["transportadora"], p["uf"])
            rota_convs[k].extend(logisticos)
            rota_pedidos[k].add(oid)
    em_transito = Counter((p["transportadora"], p["uf"]) for p in pedidos.values()
                          if p.get("status_entrega") == "em_transito")
    atrasados = Counter((p["transportadora"], p["uf"]) for p in pedidos.values()
                        if p.get("status_entrega") == "em_transito" and p.get("data_prometida")
                        and p["data_prometida"] < AGORA)
    total_rotas = sum(len(v) for v in rota_pedidos.values()) or 1
    for k in sorted(rota_pedidos, key=lambda k: -len(rota_pedidos[k]))[:5]:
        transp, uf = k
        n_ped = len(rota_pedidos[k])
        convs = rota_convs[k]
        media = len(convs) / n_ped
        alvo = em_transito[k]
        notificar = min(alvo, LIMITE_NOTIFICACOES_DIA)
        evitaveis = round(notificar * max(media - 1, 0.5), 0)
        clusters.append({
            "id": f"rota-{transp}-{uf}".lower().replace(" ", "_").replace("á", "a").replace("ú", "u"),
            "tipo": "transportadora_uf",
            "titulo": f"{transp} → {uf}: atraso e rastreio parado",
            "chave": {"transportadora": transp, "uf": uf},
            "tamanho": n_ped,
            "conversas": len(convs),
            "contatos_por_pedido": round(media, 2),
            "participacao_pct": round(100 * n_ped / total_rotas, 1),
            "custo_estimado_rs": _custo(convs),
            "custo_extrapolado_rs": round(_custo(convs) * FATOR_AMOSTRA, 2),
            "causa_provavel": (f"{transp} não cumpre o SLA para {uf}: pedidos ficam 'aguardando transferência' "
                               f"e o cliente volta a perguntar ({media:.1f} conversas por pedido)."),
            "acao_preventiva": (f"Aviso proativo com nova previsão a {notificar} clientes com pedido em trânsito "
                                f"pela {transp} para {uf} ({atrasados[k]} já com prazo vencido) e chamado em lote "
                                f"na transportadora."),
            "alvo": {"pedidos_em_transito": alvo, "ja_atrasados": atrasados[k], "notificar_hoje": notificar,
                     "limite_dia": LIMITE_NOTIFICACOES_DIA},
            "contatos_evitaveis": int(evitaveis),
            "economia_estimada_rs": round(evitaveis * _custo(convs) / max(len(convs), 1), 2),
        })

    # 2) impressora de personalização ---------------------------------------
    maq_ped: dict[str, set] = defaultdict(set)
    maq_convs: dict[str, list] = defaultdict(list)
    for oid, cs in por_pedido.items():
        p = pedidos.get(oid)
        if not p or not p.get("maquina_personalizacao"):
            continue
        hits = [c for c in cs if any(t in texto[c["conversation_id"]] for t in TERMOS_IMPRESSAO)]
        if hits:
            maq_ped[p["maquina_personalizacao"]].add(oid)
            maq_convs[p["maquina_personalizacao"]].extend(hits)
    produzidos = Counter(p["maquina_personalizacao"] for p in pedidos.values() if p.get("maquina_personalizacao"))
    maquinas = {m["maquina_id"]: m for m in _tabela("maquinas_personalizacao")}
    if maq_ped:
        taxas = {m: len(maq_ped[m]) / produzidos[m] for m in maq_ped if produzidos[m]}
        media_taxa = sum(len(v) for v in maq_ped.values()) / max(sum(produzidos.values()), 1)
        pior = max(taxas, key=taxas.get)
        info = maquinas.get(pior, {})
        recentes = Counter(pedidos[o]["data_separacao"][:7] for o in maq_ped[pior])
        pendentes = [p for p in pedidos.values() if p.get("maquina_personalizacao") == pior
                     and p.get("status_entrega") in ("em_transito", "aguardando_separacao")]
        n_ped = len(maq_ped[pior])
        evit = round(len(pendentes) * taxas[pior] * max(len(maq_convs[pior]) / n_ped, 1), 0)
        clusters.append({
            "id": f"impressora-{pior}".lower(),
            "tipo": "impressora",
            "titulo": f"Impressora {pior} ({info.get('cd', '?')}): personalização torta/borrada",
            "chave": {"maquina": pior, "cd": info.get("cd"), "modelo": info.get("fabricante_modelo")},
            "tamanho": n_ped,
            "conversas": len(maq_convs[pior]),
            "taxa_reclamacao_pct": round(100 * taxas[pior], 2),
            "taxa_media_pct": round(100 * media_taxa, 2),
            "por_maquina": {m: {"reclamacoes": len(maq_ped[m]), "produzidos": produzidos[m],
                                "taxa_pct": round(100 * taxas.get(m, 0), 2)} for m in sorted(produzidos)},
            "por_mes": dict(sorted(recentes.items())),
            "custo_estimado_rs": _custo(maq_convs[pior]),
            "custo_extrapolado_rs": round(_custo(maq_convs[pior]) * FATOR_AMOSTRA, 2),
            "causa_provavel": (f"{pior} reclama {taxas[pior] / max(media_taxa, 1e-9):.1f}× a média das impressoras; "
                               f"última preventiva em {info.get('ultima_manutencao_preventiva', '?')}, "
                               f"cartucho trocado em {info.get('ultima_troca_cartucho', '?')}."),
            "acao_preventiva": (f"Pedir preventiva/calibração da {pior} à produção e inspecionar "
                                f"{len(pendentes)} peças ainda não entregues antes de saírem do CD."),
            "alvo": {"pedidos_nao_entregues": len(pendentes)},
            "contatos_evitaveis": int(evit),
            "economia_estimada_rs": round(evit * 4.80, 2),
        })

    # 3) lote GoBeauté -------------------------------------------------------
    lote_ped: dict[str, set] = defaultdict(set)
    lote_convs: dict[str, list] = defaultdict(list)
    for oid, cs in por_pedido.items():
        p = pedidos.get(oid)
        if not p or not p.get("lote_produto"):
            continue
        hits = [c for c in cs if any(t in texto[c["conversation_id"]] for t in TERMOS_QUALIDADE)]
        if hits:
            lote_ped[p["lote_produto"]].add(oid)
            lote_convs[p["lote_produto"]].extend(hits)
    vendidos_lote = Counter(p["lote_produto"] for p in pedidos.values() if p.get("lote_produto"))
    if lote_ped:
        def score(l):  # tamanho com peso de taxa, exige volume mínimo
            return len(lote_ped[l]) * (len(lote_ped[l]) / max(vendidos_lote[l], 1))
        pior = max(lote_ped, key=score)
        skus = Counter(pedidos[o]["descricao_produto"] for o in lote_ped[pior])
        reacoes = sum(1 for c in lote_convs[pior]
                      if any(t in texto[c["conversation_id"]] for t in ("alergi", "coceira", "inchad", "irrit", "reação", "ardeu")))
        ativos = [p for p in pedidos.values() if p.get("lote_produto") == pior
                  and p.get("status_entrega") in ("em_transito", "aguardando_separacao")]
        n_ped = len(lote_ped[pior])
        taxa = n_ped / max(vendidos_lote[pior], 1)
        evit = round(len(ativos) * taxa * max(len(lote_convs[pior]) / n_ped, 1), 0)
        clusters.append({
            "id": f"lote-{pior}".lower(),
            "tipo": "lote",
            "titulo": f"Lote GoBeauté {pior}: vazamento/qualidade",
            "chave": {"lote": pior, "produtos": dict(skus.most_common(3))},
            "tamanho": n_ped,
            "conversas": len(lote_convs[pior]),
            "taxa_reclamacao_pct": round(100 * taxa, 1),
            "reacoes_adversas": reacoes,
            "custo_estimado_rs": _custo(lote_convs[pior]),
            "custo_extrapolado_rs": round(_custo(lote_convs[pior]) * FATOR_AMOSTRA, 2),
            "causa_provavel": (f"{n_ped} de {vendidos_lote[pior]} pedidos do lote {pior} reclamaram de vazamento "
                               f"ou qualidade ({100 * taxa:.0f}%): tampa/lacre do lote ou embalagem no CD."),
            "acao_preventiva": (f"Segregar o saldo do lote {pior} para inspeção da Qualidade, reforçar lacre na "
                                f"separação e avisar {len(ativos)} clientes com pedido ainda não entregue"
                                + (" (registrar cosmetovigilância das reações)." if reacoes else ".")),
            "alvo": {"pedidos_nao_entregues": len(ativos)},
            "contatos_evitaveis": int(evit),
            "economia_estimada_rs": round(evit * 4.80, 2),
        })

    # 4) brinde da campanha não enviado, por CD -----------------------------
    br_ped: dict[str, set] = defaultdict(set)
    br_convs: dict[str, list] = defaultdict(list)
    for oid, cs in por_pedido.items():
        p = pedidos.get(oid)
        if not p or not p.get("cd_separacao"):
            continue
        hits = [c for c in cs if any(t in texto[c["conversation_id"]] for t in TERMOS_BRINDE)]
        if hits:
            br_ped[p["cd_separacao"]].add(oid)
            br_convs[p["cd_separacao"]].extend(hits)
    if br_ped:
        cd = max(br_ped, key=lambda k: len(br_ped[k]))
        total = sum(len(v) for v in br_ped.values())
        elegiveis = [p for p in pedidos.values() if p.get("cd_separacao") == cd and "brinde" in (p.get("descricao_produto") or "").lower()
                     and p.get("status_entrega") in ("em_transito", "aguardando_separacao")]
        notificar = min(len(elegiveis), LIMITE_NOTIFICACOES_DIA)
        n_ped = len(br_ped[cd])
        media = len(br_convs[cd]) / n_ped
        evit = round(notificar * (n_ped / max(sum(1 for p in pedidos.values() if p.get("cd_separacao") == cd
                                                    and "brinde" in (p.get("descricao_produto") or "").lower()), 1)) * media, 0)
        clusters.append({
            "id": f"brinde-{cd}".lower(),
            "tipo": "brinde_campanha",
            "titulo": f"Brinde 'Mimo de Outubro' faltando — {cd}",
            "chave": {"cd": cd, "campanha": "Mimo de Outubro GoBeauté"},
            "tamanho": n_ped,
            "conversas": len(br_convs[cd]),
            "participacao_pct": round(100 * n_ped / total, 1),
            "por_cd": {k: len(v) for k, v in br_ped.items()},
            "custo_estimado_rs": _custo(br_convs[cd]),
            "custo_extrapolado_rs": round(_custo(br_convs[cd]) * FATOR_AMOSTRA, 2),
            "causa_provavel": (f"{100 * n_ped / total:.0f}% das queixas de brinde vêm de pedidos separados no {cd}: "
                               "a separação do CD não inclui a necessaire da campanha."),
            "acao_preventiva": (f"Checklist de brinde na expedição do {cd} e envio proativo da necessaire a "
                                f"{notificar} pedidos elegíveis ainda não entregues."),
            "alvo": {"pedidos_elegiveis": len(elegiveis), "notificar_hoje": notificar},
            "contatos_evitaveis": int(evit),
            "economia_estimada_rs": round(evit * _custo(br_convs[cd]) / max(len(br_convs[cd]), 1), 2),
        })

    # 5) cobranças em dobro --------------------------------------------------
    trans: dict[str, list] = defaultdict(list)
    for t in _tabela("pagamentos_gateway"):
        trans[t["order_id"]].append(t)
    dobro, abertos = [], []
    for oid, ts in trans.items():
        ok = [t for t in ts if t.get("status") in ("capturada", "paga", "estornada")]
        if len(ok) >= 2:
            dobro.append(oid)
            if sum(1 for t in ok if t["status"] in ("capturada", "paga")) >= 2:
                abertos.append(oid)
    if dobro:
        adq = Counter(trans[o][0].get("adquirente") or "?" for o in dobro)
        met = Counter(trans[o][0].get("metodo") or "?" for o in dobro)
        convs_dobro = [c for o in dobro for c in por_pedido.get(o, [])]
        convs_dobro += [c for c in todas if any(t in texto[c["conversation_id"]] for t in TERMOS_DOBRO)
                        and c.get("order_id") not in set(dobro)]
        valor_aberto = round(sum(_f(trans[o][0]["valor"]) for o in abertos), 2)
        clusters.append({
            "id": "cobranca-dobro",
            "tipo": "cobranca_dobro",
            "titulo": "Cobrança em dobro no gateway",
            "chave": {"adquirentes": dict(adq.most_common()), "metodos": dict(met.most_common())},
            "tamanho": len(dobro),
            "conversas": len(convs_dobro),
            "ainda_sem_estorno": len(abertos),
            "valor_sem_estorno_rs": valor_aberto,
            "custo_estimado_rs": _custo(convs_dobro),
            "custo_extrapolado_rs": round(_custo(convs_dobro) * FATOR_AMOSTRA, 2),
            "causa_provavel": ("Retentativa de captura no checkout gera segunda transação aprovada em minutos "
                               f"(principalmente {', '.join(list(adq)[:2])}); o estorno depende do cliente reclamar."),
            "acao_preventiva": (f"Estornar proativamente os {len(abertos)} pedidos ainda cobrados 2× "
                                f"(R$ {valor_aberto:.2f}), avisar o cliente antes que ele perceba e pedir "
                                "idempotência na captura ao time de pagamentos."),
            "alvo": {"pedidos_para_estornar": len(abertos), "notificar_hoje": min(len(abertos), LIMITE_NOTIFICACOES_DIA)},
            "contatos_evitaveis": len(abertos) * max(round(len(convs_dobro) / max(len(dobro), 1)), 1),
            "economia_estimada_rs": round(len(abertos) * 7.20, 2),
        })

    clusters.sort(key=lambda c: -c["custo_estimado_rs"])
    fotos = _fotos_reaproveitadas()
    if fotos["reusos"]:
        clusters.insert(0, fotos)  # primeiro insight: auditoria retroativa (os demais seguem por custo)
    repetidas = [c for cs in por_pedido.values() if len(cs) >= 2 for c in cs]
    return {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "agora_ambiente": config.NOW_ISO,
        "fonte": "sqlite" if config.DB_PATH.exists() else "csv",
        "amostra": "historico_conversas é amostra de 1 em 4 clientes; custo_extrapolado = ×4",
        "resumo": {
            "conversas": len(conversas),
            "fila_aberta": len(fila),
            "pedidos_com_contato_repetido": sum(1 for cs in por_pedido.values() if len(cs) >= 2),
            "conversas_repetidas": len(repetidas),
            "custo_repetidas_rs": _custo(repetidas),
            "contatos_evitaveis_total": sum(c["contatos_evitaveis"] for c in clusters),
            "economia_estimada_rs": round(sum(c.get("economia_estimada_rs", 0) for c in clusters), 2),
            "limite_notificacoes_dia": LIMITE_NOTIFICACOES_DIA,
        },
        "fotos_reaproveitadas": fotos,
        "clusters": clusters,
        "calculado_em_ms": int((time.perf_counter() - t0) * 1000),
    }


@lru_cache(maxsize=1)
def _cache() -> dict:
    return _calcular()


def insights(refresh: bool = False) -> dict:
    """Insights do Analista (cacheado). `refresh=True` recalcula."""
    if refresh:
        _cache.cache_clear()
    return _cache()


def cluster_do_pedido(order_id: str | None, texto: str = "") -> dict | None:
    """Encontra o cluster conhecido ao qual um pedido pertence (para anexar ao caso)."""
    if not order_id:
        return None
    ped = _pedidos_idx().get(order_id)
    if not ped:
        return None
    t = (texto or "").lower()
    for c in insights()["clusters"]:
        k = c["chave"]
        if c["tipo"] == "transportadora_uf" and ped.get("transportadora") == k["transportadora"] and ped.get("uf") == k["uf"]:
            return c
        if c["tipo"] == "impressora" and ped.get("maquina_personalizacao") == k["maquina"]:
            return c
        if c["tipo"] == "lote" and ped.get("lote_produto") == k["lote"]:
            return c
        if c["tipo"] == "brinde_campanha" and ped.get("cd_separacao") == k["cd"] and any(x in t for x in TERMOS_BRINDE):
            return c
        if c["tipo"] == "cobranca_dobro" and any(x in t for x in TERMOS_DOBRO):
            return c
    return None


@lru_cache(maxsize=1)
def _pedidos_idx() -> dict:
    return {p["order_id"]: p for p in _tabela("pedidos")}


async def run(case) -> None:
    """Anexa ao caso o cluster de causa-raiz a que ele pertence (se houver). Linha do tempo: agente "analista"."""
    import asyncio
    from agents.base import passo  # import tardio: insights() funciona sem o app

    async with passo(case, "analista", "procurando padrão de causa-raiz", atraso=False) as s:
        try:
            cl = await asyncio.to_thread(cluster_do_pedido, case.order_id, case.mensagem)
        except Exception as e:  # nunca derruba o caso
            cl = None
            s.detalhe = f"sem análise: {e}"
        if cl:
            s.status = "alerta"
            s.titulo = f"faz parte do cluster: {cl['titulo']}"
            s.detalhe = f"{cl['causa_provavel']} Ação preventiva: {cl['acao_preventiva']}"
            s.evidencias.append({"tipo": "cluster", "id": cl["id"], "tamanho": cl["tamanho"],
                                 "contatos_evitaveis": cl["contatos_evitaveis"],
                                 "custo_estimado_rs": cl["custo_estimado_rs"]})
            case.dossie.setdefault("analista", {})["cluster"] = {
                k: cl[k] for k in ("id", "titulo", "causa_provavel", "acao_preventiva", "tamanho", "contatos_evitaveis")}
        else:
            s.status = "ok"
            s.titulo = "caso isolado, sem padrão conhecido"
