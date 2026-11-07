"""Rastreador (investigação): reúne pedido, cliente, histórico e rastreio em paralelo e aponta inconsistências.

Para a transportadora sem API (HTTP 501), `tools.get_tracking` vira agente de navegador no portal.
"""
from __future__ import annotations

import asyncio

import tools
from core.state import Case

from .base import agora_ambiente, brl, narrar, parse_data, passo


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def inconsistencias(case: Case, pedido: dict, cliente: dict, rastreio: dict, historico: dict) -> list[dict]:
    out = []
    if pedido and case.customer_id and pedido.get("customer_id") and pedido["customer_id"] != case.customer_id:
        out.append({"tipo": "inconsistencia", "titulo": "pedido é de outro cliente",
                    "detalhe": f"pedido {pedido.get('order_id')} pertence a {pedido['customer_id']}, contato veio de {case.customer_id}"})
    st_ped = (pedido.get("status_entrega") or "").lower()
    st_tr = (rastreio.get("status") or "").lower()
    if st_ped and st_tr and ("entreg" in st_ped) != ("entreg" in st_tr):
        out.append({"tipo": "inconsistencia", "titulo": "ERP e transportadora discordam da entrega",
                    "detalhe": f"ERP: {st_ped} · transportadora: {st_tr}"})
    pags = pedido.get("pagamentos") or []
    capt = [p for p in pags if str(p.get("status", "")).lower() in ("capturada", "paga", "aprovado", "aprovada")]
    if len(capt) >= 2:
        out.append({"tipo": "inconsistencia", "titulo": f"{len(capt)} cobranças capturadas no mesmo pedido",
                    "detalhe": "possível cobrança em dobro: " + ", ".join(brl(p.get("valor")) for p in capt)})
    intencao = (case.triagem or {}).get("intencao")
    if intencao in ("avaria", "item_faltante", "erro_personalizacao") and st_ped and "entreg" not in st_ped \
            and "entreg" not in st_tr:
        out.append({"tipo": "inconsistencia", "titulo": "reclama do produto, mas o pedido não consta como entregue",
                    "detalhe": f"status no ERP: {st_ped or '?'}; transportadora: {st_tr or '?'}"})
    ra = [r for r in (historico.get("reclame_aqui") or []) if r.get("order_id") == pedido.get("order_id")
          and str(r.get("resolvido") or "").lower() not in ("sim", "true", "1")]
    if ra:  # sobe a prioridade e entra na resposta; NÃO é gatilho de nível D (o D olha a mensagem atual)
        out.append({"tipo": "prioridade", "titulo": f"Reclame Aqui aberto sobre este pedido ({ra[0].get('data_publicacao', '')[:10]})",
                    "detalhe": ra[0].get("titulo") or "", "reclame_aqui": ra[0].get("reclamacao_id")})
    ent = parse_data(pedido.get("data_entrega"))
    if ent and intencao in ("avaria", "item_faltante", "erro_personalizacao"):
        dias = (agora_ambiente() - ent).days
        if dias > 7:
            out.append({"tipo": "prazo", "titulo": f"recebido há {dias} dias (política: até 7)",
                        "detalhe": f"entregue em {pedido.get('data_entrega')}"})
    return out


def pedido_sugerido(historico: dict) -> dict | None:
    """Pedido mais recente do cliente (prioriza entregue/em trânsito), para a Voz perguntar "é este?"."""
    peds = [p for p in (historico or {}).get("pedidos") or [] if p.get("order_id")
            and str(p.get("data_pedido") or "") <= agora_ambiente().strftime("%Y-%m-%d")]
    if not peds:
        return None
    peds.sort(key=lambda p: (p.get("status_entrega") in ("entregue", "em_transito"), str(p.get("data_pedido") or "")),
              reverse=True)
    p = peds[0]
    prod = (p.get("descricao_produto") or "").split(" + ")[0]
    return {"order_id": p["order_id"], "produto": prod, "data_pedido": p.get("data_pedido"),
            "status_entrega": p.get("status_entrega")}


async def run(case: Case) -> None:
    async with passo(case, "rastreador", "consultando pedido, cliente e rastreio") as s:
        if not case.order_id and not case.customer_id:
            s.status = "alerta"
            s.titulo = "sem pedido nem cliente identificados"
            s.detalhe = "Nada para consultar: a Voz vai pedir o número do pedido."
            case.dossie = {"faltando": ["order_id"]}
            return
        pedido: dict = {}
        erros: list[str] = []
        status_http: dict[str, int | None] = {}
        nao_encontrado = False
        if case.order_id and not case.customer_id:
            try:
                pedido = await tools.get_order(case, case.order_id)
                case.customer_id = pedido.get("customer_id")
            except tools.ToolError as e:
                nao_encontrado = e.status == 404
                if not nao_encontrado:
                    erros.append(str(e))

        async def _safe(nome, coro):
            try:
                return await coro
            except tools.ToolError as e:
                status_http[nome] = e.status
                if not (nome in ("pedido", "rastreio") and e.status == 404):
                    erros.append(str(e))
                return {}

        data_prometida = pedido.get("data_prometida") if pedido else None
        tarefas = []
        nomes = []
        if case.order_id and not pedido and not nao_encontrado:
            tarefas.append(_safe("pedido", tools.get_order(case, case.order_id)))
            nomes.append("pedido")
        if case.customer_id:
            tarefas += [_safe("cliente", tools.get_customer(case, case.customer_id)),
                        _safe("historico", tools.get_customer_history(case, case.customer_id))]
            nomes += ["cliente", "historico"]
        if case.order_id and not nao_encontrado:
            tarefas.append(_safe("rastreio", tools.get_tracking(case, case.order_id, data_prometida=data_prometida)))
            nomes.append("rastreio")
        res = dict(zip(nomes, await asyncio.gather(*tarefas)))  # fan-out paralelo
        pedido = pedido or res.get("pedido") or {}
        cliente = res.get("cliente") or {}
        historico = res.get("historico") or {}
        rastreio = res.get("rastreio") or {}
        if case.order_id and not pedido and (nao_encontrado or status_http.get("pedido") == 404):
            nao_encontrado = True
            rastreio = {}        # sem pedido, ninguém afirma status de entrega
        # o rastreio pode ter rodado antes de sabermos a data prometida: recalcula atraso
        if rastreio and pedido.get("data_prometida") and "dias_atraso" not in rastreio:
            tools._calc_atraso(rastreio, pedido.get("data_prometida"))
        if cliente.get("nome"):
            case.cliente_nome = cliente["nome"]

        inc = inconsistencias(case, pedido, cliente, rastreio, historico)
        case.dossie = {
            "pedido": pedido, "cliente": cliente, "historico": historico, "rastreio": rastreio,
            "fonte_rastreio": rastreio.get("fonte"), "inconsistencias": inc, "erros": erros,
        }
        if nao_encontrado:
            case.dossie["pedido_nao_encontrado"] = {"numero_informado": case.order_id}
        elif not pedido:
            case.dossie["pedido_nao_informado"] = True
        sug = pedido_sugerido(historico) if not pedido else None
        if sug:
            case.dossie["pedido_sugerido"] = sug

        # linha do tempo
        partes = []
        if pedido:
            partes.append(f"pedido {pedido.get('order_id')} {brl(pedido.get('valor'))}")
            s.evidencias.append({"tipo": "pedido", "titulo": f"pedido {pedido.get('order_id')}",
                                 "detalhe": f"{pedido.get('descricao_produto', '')} · {brl(pedido.get('valor'))} · "
                                            f"{pedido.get('status_entrega', '')} · {pedido.get('transportadora', '')}",
                                 "ref": f"/api/erp/pedidos/{pedido.get('order_id')}"})
        if cliente:
            hist = historico or {}
            s.evidencias.append({"tipo": "cliente", "titulo": f"cliente {cliente.get('nome', case.customer_id)}",
                                 "detalhe": f"{cliente.get('n_pedidos', '?')} pedidos · {cliente.get('n_trocas_12m', 0)} trocas/12m · "
                                            f"{cliente.get('qtd_reclamacoes_90d', 0)} reclamações/90d · "
                                            f"{len(hist.get('anexos') or [])} fotos já enviadas"
                                            + (" · FLAG DE RISCO" if cliente.get("flag_risco") else ""),
                                 "ref": f"/api/erp/clientes/{case.customer_id}"})
        if rastreio:
            fonte = rastreio.get("fonte")
            atraso = f", {rastreio.get('dias_atraso')} dia(s) de atraso" if rastreio.get("atrasado") else ""
            s.evidencias.append({"tipo": "rastreio",
                                 "titulo": ("rastreio lido no portal (API indisponível)" if fonte == "portal"
                                            else "rastreio pela API"),
                                 "detalhe": f"{rastreio.get('transportadora') or pedido.get('transportadora', '')}: "
                                            f"{rastreio.get('status', '?')}{atraso}; previsão {rastreio.get('previsao_entrega') or '?'}",
                                 "eventos": rastreio.get("eventos", [])[-6:],
                                 "navegacao": rastreio.get("navegacao")})
            partes.append(f"{str(rastreio.get('status') or '?').replace('_', ' ')}{atraso}"
                          + (f" ({rastreio.get('transportadora')})" if rastreio.get("transportadora") else ""))
        s.evidencias.extend(inc)
        if erros:
            s.detalhe = "Falhas: " + " | ".join(erros)
        if rastreio.get("fonte") == "portal":
            s.titulo = f"API indisponível → li o rastreio no portal da {rastreio.get('transportadora') or 'transportadora'}"
            if rastreio.get("atrasado"):
                s.titulo += f": {rastreio.get('dias_atraso')} dia(s) de atraso"
        elif any(i["tipo"] == "inconsistencia" for i in inc):
            s.titulo = next(i["titulo"] for i in inc if i["tipo"] == "inconsistencia")
        else:
            s.titulo = ("dossiê pronto: " + " · ".join(partes[:2])) if partes else "dossiê pronto"
        if any(i["tipo"] != "prioridade" for i in inc) or erros:
            s.status = "alerta"
        if any(i["tipo"] == "prioridade" for i in inc):
            case.triagem["urgencia"] = "alta"
            case.triagem["reclame_aqui_aberto"] = True
        if nao_encontrado:
            s.status = "alerta"
            s.titulo = f"não achei o pedido {case.order_id}"
            s.detalhe = (f"O ERP não tem o pedido {case.order_id}. Nenhum status de entrega será informado sem o pedido; "
                         "a Voz vai pedir o número certo" + (f" e sugerir o {sug['order_id']}." if sug else "."))
            s.evidencias.insert(0, {"tipo": "pedido_nao_encontrado", "titulo": f"pedido {case.order_id} não existe no ERP",
                                    "detalhe": "GET /api/erp/pedidos → 404", "ref": f"/api/erp/pedidos/{case.order_id}"})
        elif not pedido and case.customer_id:
            s.status = "alerta"
            s.titulo = "cliente identificado, mas sem número de pedido"
        await narrar(case)
