"""Detetive (veracidade): perícia da foto + histórico + rastreio → score 0–100 com evidências explicáveis.

A pontuação é determinística e auditável: cada evidência tem um `peso` em pontos e a soma explica o score.
O LLM (opcional) só redige a justificativa em linguagem natural; nunca muda o número.
Quando a foto já apareceu em outro pedido, monta `comparacao` lado a lado (Aposta 1 da SPEC).
"""
from __future__ import annotations

import asyncio
import re

import tools
from core import llm
from core.state import Case

from core.state import store

from .base import abrir_passo, como, fechar_passo, narrar, parse_data
from .politica import carregar, faixa

BASE = 70  # presunção de boa-fé (custo de erro assimétrico: o honesto não começa suspeito)
INTENCOES_FOTO = ("avaria", "item_faltante", "erro_personalizacao")


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ev(peso: int, titulo: str, detalhe: str = "", tipo: str = "sinal", **kw) -> dict:
    return {"tipo": tipo, "peso": peso, "titulo": titulo, "detalhe": detalhe, **kw}


def _palavras(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ú]{4,}", (s or "").lower())}


def _comparacao(atual: dict, original: dict, exif: dict, outros: list[dict] | None = None) -> dict:
    novas = sorted(_palavras(atual.get("descricao_visual")) - _palavras(original.get("descricao_visual")))
    dif = []
    if novas:
        dif.append("na foto nova aparece: " + ", ".join(novas[:8]))
    if atual.get("exif_software"):
        dif.append(f"metadado de software: {atual['exif_software']}")
    if original.get("customer_id") and atual.get("customer_id") and original["customer_id"] != atual["customer_id"]:
        dif.append("enviada antes por outro cliente")
    elif original.get("order_id") != atual.get("order_id"):
        dif.append("enviada antes pelo mesmo cliente em outro pedido")
    return {
        "mesmo_hash": True,
        "similaridade": 1.0,  # hash perceptual idêntico = visualmente a mesma imagem
        "hash_perceptual": atual.get("hash_perceptual"),
        "original": {k: original.get(k) for k in ("anexo_id", "order_id", "customer_id", "conversation_id", "enviado_em",
                                                   "descricao_visual", "exif_data_captura", "exif_modelo_camera",
                                                   "exif_software", "url")},
        "atual": {k: atual.get(k) for k in ("anexo_id", "order_id", "customer_id", "conversation_id", "enviado_em",
                                             "descricao_visual", "exif_data_captura", "exif_modelo_camera",
                                             "exif_software", "url")},
        "diferencas": dif,
        "indicios_ia": exif.get("indicios_ia") or [],
        # todos os usos anteriores do mesmo hash (o `original` é o primeiro)
        "outros_usos": [{k: o.get(k) for k in ("anexo_id", "order_id", "customer_id", "enviado_em")}
                        for o in (outros or [original])],
    }


async def periciar(case: Case) -> list[dict]:
    """Perícia de cada anexo (roda em paralelo com o Rastreador)."""
    if not case.anexos:
        return []

    async def um(anexo):
        try:
            p = await tools.analyze_damage_photo(case, anexo)
        except tools.ToolError as e:
            return {"anexo": anexo.model_dump(), "erro": str(e)}
        originais = [dict(r) for r in (p.get("reuso") or [])]
        if originais and anexo.hash_perceptual:  # busca reversa: todas as fotos com o mesmo hash
            try:
                achados = {a.get("anexo_id"): a for a in await tools.get_attachments(case, hash=anexo.hash_perceptual)}
                for o in originais:
                    o.update({k: v for k, v in (achados.get(o.get("anexo_id")) or {}).items() if v not in (None, "")})
            except tools.ToolError:
                pass
        return {"anexo": anexo.model_dump(), "pericia": p, "originais": originais}

    return list(await asyncio.gather(*(um(a) for a in case.anexos)))


def pontuar(case: Case, pericias: list[dict]) -> dict:
    """Score determinístico. Retorna {score, evidencias, comparacao, fraude_objetiva}."""
    evid: list[dict] = []
    comparacao = None
    fraude_objetiva = False
    d = case.dossie or {}
    pedido, cliente, rastreio = d.get("pedido") or {}, d.get("cliente") or {}, d.get("rastreio") or {}
    intencao = (case.triagem or {}).get("intencao")

    if intencao in ("atraso", "rastreio"):
        if rastreio.get("atrasado"):
            evid.append(_ev(25, f"atraso confirmado: {rastreio.get('dias_atraso')} dia(s) após a data prometida",
                            f"data prometida {pedido.get('data_prometida') or rastreio.get('data_prometida')}, "
                            f"último evento: {rastreio.get('status')}", "rastreio"))
        elif rastreio:
            evid.append(_ev(-10, "rastreio dentro do prazo", f"previsão {rastreio.get('previsao_entrega')}", "rastreio"))
        if rastreio.get("fonte") == "portal":
            evid.append(_ev(0, "eventos lidos no portal da transportadora", "API sem rastreio (501)", "fonte"))

    if intencao == "cobranca_duplicada":
        from .politica import capturas
        capt = capturas(pedido)
        if len(capt) >= 2:
            evid.append(_ev(35, f"{len(capt)} cobranças capturadas no gateway",
                            "; ".join(f"{p.get('transacao_id')} {p.get('metodo')} R$ {p.get('valor')} ({p.get('status')})"
                                      for p in capt), "gateway"))
        elif pedido:
            evid.append(_ev(-30, "gateway mostra uma única cobrança capturada",
                            f"{len(pedido.get('pagamentos') or [])} transação(ões) no pedido", "gateway"))

    for item in pericias:
        a = item["anexo"]
        p = item.get("pericia")
        if not p:
            evid.append(_ev(0, "perícia indisponível", item.get("erro", ""), "erro"))
            continue
        ex = p.get("exif") or {}
        conf = _f(p.get("confianca"), 0.5)
        desc_f = (p.get("descricao_visual") or a.get("descricao_visual") or "").lower()
        if intencao == "item_faltante" and re.search(r"n[aã]o h[aá]|sem (o|a|nenhum)|faltand|n[aã]o veio|ausente|vazi[ao]", desc_f):
            evid.append(_ev(10, "foto mostra a caixa sem o item", p.get("descricao_visual") or "", "foto",
                            anexo_id=a.get("anexo_id")))
        elif p.get("avaria_detectada"):
            peso = round((conf - 0.6) * 50)
            evid.append(_ev(peso, f"avaria visível na foto (confiança {conf:.0%})", p.get("descricao_visual") or
                            a.get("descricao_visual") or "", "foto", anexo_id=a.get("anexo_id")))
        else:
            evid.append(_ev(-15, "foto não mostra a avaria descrita", p.get("descricao_visual") or "", "foto",
                            anexo_id=a.get("anexo_id")))
        if p.get("produto_confere") is False:
            evid.append(_ev(-15, "produto da foto não confere com o pedido", pedido.get("descricao_produto", ""), "foto"))
        elif p.get("produto_confere"):
            evid.append(_ev(3, "produto da foto confere com o pedido", pedido.get("descricao_produto", ""), "foto"))
        reuso = [r for r in (p.get("reuso") or []) if r.get("anexo_id") != a.get("anexo_id")]
        if reuso:
            fraude_objetiva = True
            r0 = item["originais"][0] if item.get("originais") else reuso[0]
            outro_cliente = r0.get("customer_id") and r0.get("customer_id") != case.customer_id
            evid.append(_ev(-40 if outro_cliente else -35,
                            "foto reaproveitada de outro pedido" + (" (outro cliente)" if outro_cliente else ""),
                            f"mesma imagem (hash {a.get('hash_perceptual')}) enviada em {r0.get('enviado_em', '?')} "
                            f"no pedido {r0.get('order_id')}" + (f" por {r0.get('customer_id')}" if outro_cliente else ""),
                            "reuso", ref=f"/api/erp/anexos?hash={a.get('hash_perceptual')}"))
            comparacao = _comparacao({**a, "order_id": case.order_id, "customer_id": case.customer_id,
                                      "conversation_id": case.conversation_id}, r0, p,
                                     item.get("originais") or reuso)
        if ex.get("software_suspeito"):
            fraude_objetiva = True
            evid.append(_ev(-25, f"imagem editada ({a.get('exif_software') or ex.get('software') or 'editor/IA'})",
                            "o metadado indica editor de imagem ou gerador de IA", "exif"))
        ia = p.get("indicios_ia") or []
        if ia:
            evid.append(_ev(-min(20, 8 * len(ia)), "indícios de geração por IA", "; ".join(ia), "ia"))
        if ex.get("presente"):
            if ex.get("antes_da_entrega"):
                fraude_objetiva = True
                evid.append(_ev(-25, "foto tirada antes da entrega",
                                f"captura {ex.get('data_captura')} · entrega {pedido.get('data_entrega')}", "exif"))
            else:
                evid.append(_ev(8, "foto capturada depois da entrega",
                                f"captura {ex.get('data_captura')} · {ex.get('modelo') or ''}", "exif"))
        else:
            whats = case.canal in ("whatsapp", "telegram")
            evid.append(_ev(0 if whats else -8, "foto sem metadados (EXIF)",
                            "normal no WhatsApp, que remove os metadados" if whats else
                            "o arquivo chegou sem data de captura nem câmera", "exif"))

    if intencao in INTENCOES_FOTO:
        st = (pedido.get("status_entrega") or "").lower()
        if st and "entreg" not in st and "entreg" not in (rastreio.get("status") or "").lower():
            evid.append(_ev(-15, "pedido ainda não consta como entregue", f"status: {st}", "rastreio"))
    # histórico do cliente
    hist = d.get("historico") or {}
    tr = int(_f(cliente.get("n_trocas_12m")))
    if cliente.get("flag_risco"):
        # não contar em dobro: motivos que já viraram evidência própria (trocas em 12m, a própria foto do caso)
        meus = {a.anexo_id for a in case.anexos if a.anexo_id}
        outros = []
        for m in cliente.get("motivos_risco") or ["flag de risco no CRM"]:
            if "trocas/reenvios em 12 meses" in m and tr >= 3:
                continue
            if "foto já usada" in m:
                ids = set(re.findall(r"[A-Z]+-?[A-Z]*\d+", m.split("(", 1)[-1]))
                if ids and ids <= meus:
                    continue
            outros.append(m)
        if outros:
            evid.append(_ev(-10, "cliente com alerta de risco", "; ".join(outros), "historico"))
    if tr >= 3:
        evid.append(_ev(-8, f"{tr} trocas/reenvios em 12 meses", "", "historico"))
    rq = int(_f(cliente.get("qtd_reclamacoes_90d")))
    if rq >= 3:
        evid.append(_ev(-8, f"{rq} reclamações em 90 dias", "", "historico"))
    if int(_f(cliente.get("n_pedidos"))) >= 3 and tr == 0 and not cliente.get("flag_risco"):
        evid.append(_ev(5, f"cliente fiel: {cliente.get('n_pedidos')} pedidos, nenhuma troca", "", "historico"))
    ra = hist.get("reclame_aqui") or []
    if len(ra) >= 2:
        evid.append(_ev(-3, f"{len(ra)} reclamações no Reclame Aqui", "", "historico"))

    score = max(0, min(100, BASE + sum(e["peso"] for e in evid)))
    return {"score": score, "base": BASE, "evidencias": evid, "comparacao": comparacao,
            "fraude_objetiva": fraude_objetiva}


def _justificativa_regras(v: dict) -> str:
    contra = [e for e in v["evidencias"] if e["peso"] < 0]
    favor = [e for e in v["evidencias"] if e["peso"] > 0]
    partes = [f"Score {v['score']}/100 (faixa {v['faixa']}; base {v['base']} de boa-fé)."]
    if contra:
        partes.append("Pesou contra: " + "; ".join(f"{e['titulo']} ({e['peso']})" for e in contra) + ".")
    if favor:
        partes.append("A favor: " + "; ".join(f"{e['titulo']} (+{e['peso']})" for e in favor) + ".")
    return " ".join(partes)


async def _justificativa(case: Case, v: dict) -> str:
    base = _justificativa_regras(v)
    if not llm.enabled():
        return base
    try:
        txt = await llm.chat([
            {"role": "system", "content": "Você é o Detetive do Goxy. Explique em até 3 frases, em português, "
             "por que o score de veracidade ficou neste valor, citando só as evidências fornecidas (não invente "
             "fatos, não mude o número, não acuse o cliente)."},
            {"role": "user", "content": f"Evidências e pesos: {base}"},
        ], case=case, agent="detetive", model=None, max_tokens=2000, temperature=0.2)
        return txt.strip() or base
    except Exception:
        return base


SEM_NOTA = {"reacao_adversa": "reação adversa: segurança primeiro, não se pontua",
            "cancelamento": "arrependimento é direito do cliente (CDC art. 49), não depende de nota"}


def _precisa(case: Case) -> bool:
    intencao = (case.triagem or {}).get("intencao")
    return (intencao in INTENCOES_FOTO or intencao in ("atraso", "reembolso", "troca", "cobranca_duplicada")
            or bool(case.anexos)) and intencao not in SEM_NOTA


def _passo_aberto(case: Case):
    return next((s for s in reversed(case.timeline) if s.agent == "detetive" and s.status == "rodando"), None)


async def preparar(case: Case) -> None:
    """Nó `detetive_prep` (em paralelo com o Rastreador): abre o passo e faz a perícia das fotos."""
    if not _precisa(case):
        s = case.step("detetive")
        s.status, s.titulo = "pulado", SEM_NOTA.get((case.triagem or {}).get("intencao"), "sem reclamação para periciar")
        await store.save(case)
        return
    s = await abrir_passo(case, "detetive", "periciando a evidência" if case.anexos else "conferindo a reclamação")
    with como("detetive", s):
        try:
            pericias = await periciar(case)
        except Exception as e:
            s.status, s.detalhe = "falhou", f"{type(e).__name__}: {e}"
            await fechar_passo(case, s)
            raise
        case.veracidade = {"pericias": pericias, "em_andamento": True}
        if case.anexos:
            await narrar(case, titulo="perícia da foto feita, esperando o dossiê do Rastreador")
        else:
            await store.save(case)


async def concluir(case: Case) -> None:
    """Nó `detetive` (depois do Rastreador e da perícia): pontua com o dossiê e fecha o passo."""
    s = _passo_aberto(case)
    if s is None:
        return
    intencao = (case.triagem or {}).get("intencao")
    pericias = (case.veracidade or {}).get("pericias") or []
    with como("detetive", s):
        try:
            await _concluir(case, s, intencao, pericias)
        except Exception as e:
            s.status, s.detalhe = "falhou", f"{type(e).__name__}: {e}"
            raise
        finally:
            await fechar_passo(case, s)


async def _concluir(case: Case, s, intencao, pericias) -> None:
    if intencao in INTENCOES_FOTO and not case.anexos:
        case.veracidade = {"score": None, "faixa": "sem_score", "evidencias": [],
                           "justificativa": "Reclamação sem foto: a política exige foto antes de reenviar."}
        s.status, s.titulo = "alerta", "sem foto: vou pedir ao cliente"
        return
    if True:
        v = pontuar(case, pericias)
        v["faixa"] = faixa(v["score"], carregar())
        v["pericias"] = pericias
        v["justificativa"] = await _justificativa(case, v)
        case.veracidade = v
        s.evidencias = sorted(v["evidencias"], key=lambda e: e["peso"])
        if v["comparacao"]:
            s.evidencias.insert(0, {"tipo": "comparacao", "titulo": "comparação lado a lado",
                                    "detalhe": "; ".join(v["comparacao"]["diferencas"]),
                                    "comparacao": v["comparacao"]})
        s.detalhe = v["justificativa"]
        pior = min(v["evidencias"], key=lambda e: e["peso"], default=None)
        melhor = max(v["evidencias"], key=lambda e: e["peso"], default=None)
        if v["faixa"] == "vermelha":
            s.status = "alerta"
            s.titulo = f"{pior['titulo']} · score {v['score']}" if pior else f"score {v['score']}"
        elif v["faixa"] == "amarela":
            s.status = "alerta"
            s.titulo = f"evidência duvidosa · score {v['score']}" + (f" ({pior['titulo']})" if pior and pior["peso"] < 0 else "")
        else:
            if intencao in ("atraso", "rastreio"):
                s.titulo = f"{melhor['titulo']} · score {v['score']}" if melhor else f"score {v['score']}"
            else:
                s.titulo = (f"foto inédita, {melhor['titulo']} · score {v['score']}" if case.anexos and melhor
                            else f"reclamação procede · score {v['score']}")
