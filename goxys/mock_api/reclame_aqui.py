"""Reclame Aqui simulado (CONTRACTS §9): API JSON em /api/reclameaqui e portal público em /reclameaqui.

Lê as reclamações reais (`reclame_aqui` + texto em `historico_mensagens`) e escreve só em tabelas próprias:
`sim_ra_respostas` (respostas públicas e mensagens privadas da empresa) e `sim_ra_novas` (reclamações
publicadas pela demo e vínculo reclamação ↔ caso do Goxy). Resposta pública SEMPRE exige aprovação humana.

O contrato imita uma API de parceiro: trocar pela integração oficial = mudar a URL base e a autenticação.
"""
from __future__ import annotations

import html
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse

from core.bus import bus
from mock_api import routes as R

_DDL = {
    "sim_ra_respostas": """resposta_id TEXT PRIMARY KEY, reclamacao_id TEXT, tipo TEXT, texto TEXT, autor TEXT,
        aprovacao_id TEXT, case_id TEXT, publicado_em TEXT""",
    "sim_ra_novas": """reclamacao_id TEXT PRIMARY KEY, origem TEXT, conversation_id TEXT, customer_id TEXT, order_id TEXT,
        empresa TEXT, titulo TEXT, texto TEXT, cidade TEXT, uf TEXT, data_publicacao TEXT, case_id TEXT,
        importado_em TEXT""",
}
_prontos: set[str] = set()
APROVADAS = {"aprovada", "ajustada"}


def garantir_tabelas() -> None:
    chave = str(R.DB_PATH)
    if chave in _prontos:
        return
    for nome, ddl in _DDL.items():
        R._exec(f"CREATE TABLE IF NOT EXISTS {nome} ({ddl})")
    R._exec("CREATE INDEX IF NOT EXISTS ix_sim_ra_resp ON sim_ra_respostas(reclamacao_id)")
    _prontos.add(chave)


# ------------------------------------------------------------------ leitura

_SQL_REAIS = """
SELECT r.*, (SELECT h.texto FROM historico_mensagens h WHERE h.conversation_id = r.conversation_id
             AND h.autor = 'cliente' ORDER BY h.timestamp LIMIT 1) AS texto,
       'base' AS origem, n.case_id AS case_id, n.importado_em AS importado_em,
       (n.reclamacao_id IS NOT NULL) AS vinculada
FROM reclame_aqui r LEFT JOIN sim_ra_novas n ON n.reclamacao_id = CAST(r.reclamacao_id AS TEXT)
"""


def _limpar_texto(titulo: str, texto: Optional[str]) -> str:
    t = (texto or "").strip()
    pref = f"[{titulo}]"
    return t[len(pref):].strip() if titulo and t.startswith(pref) else t


def _respostas(rid: Optional[str] = None) -> dict[str, list[dict]]:
    sql, p = "SELECT * FROM sim_ra_respostas", []
    if rid:
        sql += " WHERE reclamacao_id=?"
        p.append(rid)
    out: dict[str, list[dict]] = {}
    for r in R._q(sql + " ORDER BY publicado_em", p):
        out.setdefault(r["reclamacao_id"], []).append(r)
    return out


def _montar(r: dict, resp: list[dict]) -> dict:
    publicas = [x for x in resp if x["tipo"] == "publica"]
    status = r.get("status_publico") or "Não respondida"
    primeira = r.get("data_primeira_resposta_empresa")
    if publicas:
        if status == "Não respondida":
            status = "Respondida"
        if not primeira:
            primeira = publicas[0]["publicado_em"]
    return {
        "reclamacao_id": str(r["reclamacao_id"]), "origem": r.get("origem"), "empresa": r.get("empresa"),
        "titulo": r.get("titulo"), "texto": _limpar_texto(r.get("titulo") or "", r.get("texto")),
        "cidade": r.get("cidade"), "uf": r.get("uf"), "data_publicacao": r.get("data_publicacao"),
        "status_publico": status, "data_primeira_resposta_empresa": primeira,
        "replicas_consumidor": r.get("replicas_consumidor") or 0, "resolvido": r.get("resolvido"),
        "nota_consumidor": r.get("nota_consumidor"), "voltaria_a_fazer_negocio": r.get("voltaria_a_fazer_negocio"),
        "data_avaliacao": r.get("data_avaliacao"), "conversation_id": r.get("conversation_id"),
        "customer_id": r.get("customer_id"), "order_id": r.get("order_id"), "case_id": r.get("case_id"),
        "importado_em": r.get("importado_em"), "vinculada": bool(r.get("vinculada")),
        "respostas_publicas": len(publicas), "respondida_por_goxys": bool(publicas),
    }


def todas() -> list[dict]:
    garantir_tabelas()
    resp = _respostas()
    rows = R._q(_SQL_REAIS)
    rows += R._q("SELECT *, NULL AS status_publico, 1 AS vinculada FROM sim_ra_novas WHERE origem='simulada'")
    out = [_montar(r, resp.get(str(r["reclamacao_id"]), [])) for r in rows]
    out.sort(key=lambda x: x["data_publicacao"] or "", reverse=True)
    return out


def uma(rid: str) -> Optional[dict]:
    return next((x for x in todas() if x["reclamacao_id"] == str(rid)), None)


def _historico(rec: dict) -> list[dict]:
    hist: list[dict] = []
    if rec["origem"] == "base" and rec.get("conversation_id"):
        for m in R._q("SELECT * FROM historico_mensagens WHERE conversation_id=? ORDER BY timestamp",
                      (rec["conversation_id"],)):
            hist.append({"autor": "consumidor" if m["autor"] == "cliente" else "empresa", "data": m["timestamp"],
                         "texto": _limpar_texto(rec["titulo"] or "", m["texto"]), "origem": "base"})
        if hist and hist[0]["autor"] == "consumidor":
            hist = hist[1:]                       # a 1ª mensagem do cliente é o próprio relato
    for x in _respostas(rec["reclamacao_id"]).get(rec["reclamacao_id"], []):
        if x["tipo"] == "publica":
            hist.append({"autor": "empresa", "data": x["publicado_em"], "texto": x["texto"], "origem": "goxys",
                         "selo": "Respondida pela empresa", "resposta_id": x["resposta_id"],
                         "aprovacao_id": x["aprovacao_id"]})
    hist.sort(key=lambda h: h["data"] or "")
    return hist


def reputacao(empresa: Optional[str] = "Gocase") -> dict:
    recs = [r for r in todas() if not empresa or empresa.lower() == "todas" or (r["empresa"] or "").lower() == empresa.lower()]
    total = len(recs)
    respondidas = [r for r in recs if r["data_primeira_resposta_empresa"]]
    avaliadas = [r for r in recs if r["resolvido"] in ("sim", "não")]
    notas = [r["nota_consumidor"] for r in recs if r["nota_consumidor"] is not None]
    horas = []
    for r in respondidas:
        a, b = R._parse(r["data_publicacao"]), R._parse(r["data_primeira_resposta_empresa"])
        if a and b and b >= a:
            horas.append((b - a).total_seconds() / 3600)

    def pct(n: int, d: int) -> float:
        return round(100 * n / d, 1) if d else 0.0

    p_resp = pct(len(respondidas), total)
    p_resol = pct(sum(r["resolvido"] == "sim" for r in avaliadas), len(avaliadas))
    p_volt = pct(sum(r["voltaria_a_fazer_negocio"] == "sim" for r in avaliadas), len(avaliadas))
    nota = round(sum(notas) / len(notas), 2) if notas else None
    # índice 0..10 no estilo dos sites de reclamação: respondidas×2 + nota×3 + resolvidas×3 + voltaria×2
    indice = round((p_resp / 10 * 2 + (nota or 0) * 3 + p_resol / 10 * 3 + p_volt / 10 * 2) / 10, 1)
    selo = ("Ótima" if indice >= 8 else "Boa" if indice >= 7 else "Regular" if indice >= 6
            else "Ruim" if indice >= 5 else "Não recomendada")
    tm = round(sum(horas) / len(horas), 1) if horas else None
    return {
        "empresa": empresa or "todas", "total_reclamacoes": total, "respondidas": len(respondidas),
        "pct_respondidas": p_resp, "avaliadas": len(avaliadas), "pct_resolvidas": p_resol,
        "pct_voltaria_a_fazer_negocio": p_volt, "nota_media_consumidor": nota,
        "tempo_medio_primeira_resposta_h": tm,
        "tempo_medio_primeira_resposta_txt": (f"{int(tm // 24)} dias e {int(tm % 24)} h" if tm and tm >= 24
                                              else f"{tm} h" if tm is not None else "-"),
        "respostas_goxys": sum(r["respostas_publicas"] for r in recs),
        "indice_reputacao": indice, "classificacao": selo,
        "nao_respondidas": total - len(respondidas),
    }


# ------------------------------------------------------------------ API JSON

api = APIRouter(prefix="/api/reclameaqui", tags=["Reclame Aqui (simulado)"])


@api.get("/reclamacoes")
def list_reclamacoes(status: Optional[str] = None, desde: Optional[str] = None, empresa: Optional[str] = None,
                     case_id: Optional[str] = None, importacao: Optional[str] = None,
                     limite: int = Query(500, le=5000)):
    out = todas()
    if status:
        out = [r for r in out if (r["status_publico"] or "").lower() == status.lower()]
    if desde:
        out = [r for r in out if (r["data_publicacao"] or "") >= desde]
    if empresa:
        out = [r for r in out if (r["empresa"] or "").lower() == empresa.lower()]
    if case_id:
        out = [r for r in out if r["case_id"] == case_id]
    if importacao == "pendente":                   # publicadas/enfileiradas e ainda sem caso no Goxy
        out = [r for r in out if r["vinculada"] and not r["case_id"]]
    return out[:limite]


@api.get("/reputacao")
def get_reputacao(empresa: str = "Gocase"):
    return reputacao(empresa)


@api.get("/reclamacoes/{reclamacao_id}")
def get_reclamacao(reclamacao_id: str):
    rec = uma(reclamacao_id)
    if not rec:
        return R._erro(404, "reclamacao_nao_encontrada", f"Reclamação {reclamacao_id} não existe")
    privadas = [x for x in _respostas(rec["reclamacao_id"]).get(rec["reclamacao_id"], []) if x["tipo"] == "privada"]
    return {**rec, "historico_publico": _historico(rec), "mensagens_privadas": privadas}


def _gravar_resposta(request: Request, rid: str, tipo: str, body: dict) -> dict:
    key, anterior = R._idem_anterior(request)
    if anterior is not None:
        return anterior
    res = {"resposta_id": f"RAR-{R._agora():%y%m%d}-{secrets.token_hex(3).upper()}", "reclamacao_id": rid,
           "tipo": tipo, "texto": body["texto"].strip(), "autor": request.headers.get("X-Agente") or "goxys",
           "aprovacao_id": body.get("aprovacao_id"), "case_id": R._case_id(request, body),
           "publicado_em": R._iso(R._agora())}
    R._insert("sim_ra_respostas", res)
    out = {**res, "idempotente": False}
    if tipo == "publica":
        out["selo"] = "Respondida pela empresa"
    R._log(request, "reclame_aqui", f"resposta_{tipo}", body, 200, out, res["case_id"], key)
    return out


@api.post("/reclamacoes/{reclamacao_id}/resposta-publica")
def post_resposta_publica(reclamacao_id: str, request: Request, body: dict = Body(...)):
    garantir_tabelas()
    if not uma(reclamacao_id):
        return R._erro(404, "reclamacao_nao_encontrada", f"Reclamação {reclamacao_id} não existe")
    if not (body.get("texto") or "").strip():
        return R._erro(422, "texto_vazio", "texto é obrigatório")
    aid = body.get("aprovacao_id")
    ap = R._q1("SELECT status FROM sim_aprovacoes WHERE aprovacao_id=?", (aid,)) if aid else None
    if not ap or ap["status"] not in APROVADAS:
        r = R._erro(403, "exige_aprovacao", "Resposta pública no Reclame Aqui exige aprovação humana aprovada",
                    aprovacao_id=aid, status_aprovacao=(ap or {}).get("status"))
        R._log(request, "reclame_aqui", "resposta_publica", body, 403, {"erro": "exige_aprovacao"}, R._case_id(request, body))
        return r
    return _gravar_resposta(request, reclamacao_id, "publica", body)


@api.post("/reclamacoes/{reclamacao_id}/mensagem-privada")
def post_mensagem_privada(reclamacao_id: str, request: Request, body: dict = Body(...)):
    garantir_tabelas()
    if not uma(reclamacao_id):
        return R._erro(404, "reclamacao_nao_encontrada", f"Reclamação {reclamacao_id} não existe")
    if not (body.get("texto") or "").strip():
        return R._erro(422, "texto_vazio", "texto é obrigatório")
    return _gravar_resposta(request, reclamacao_id, "privada", body)


@api.post("/reclamacoes/{reclamacao_id}/vinculo")
def post_vinculo(reclamacao_id: str, request: Request, body: dict = Body(...)):
    """Lado Goxy (não existe na API oficial): guarda o vínculo reclamação ↔ caso."""
    rec = uma(reclamacao_id)
    if not rec:
        return R._erro(404, "reclamacao_nao_encontrada", f"Reclamação {reclamacao_id} não existe")
    agora = R._iso(R._agora())
    if rec["vinculada"]:
        R._exec("UPDATE sim_ra_novas SET case_id=?, importado_em=? WHERE reclamacao_id=?",
                (body.get("case_id"), agora, reclamacao_id))
    else:
        R._insert("sim_ra_novas", {"reclamacao_id": reclamacao_id, "origem": "fila", "case_id": body.get("case_id"),
                                   "conversation_id": rec["conversation_id"], "importado_em": agora})
    res = {"reclamacao_id": reclamacao_id, "case_id": body.get("case_id"), "importado_em": agora}
    R._log(request, "reclame_aqui", "vincular_caso", body, 200, res, body.get("case_id"))
    return res


@api.post("/simular-nova")
async def post_simular_nova(request: Request, body: dict = Body(...)):
    garantir_tabelas()
    key, anterior = R._idem_anterior(request)
    if anterior is not None:
        return anterior
    if not (body.get("titulo") or "").strip() or not (body.get("texto") or "").strip():
        return R._erro(422, "campos_obrigatorios", "titulo e texto são obrigatórios")
    cli = R._q1("SELECT * FROM clientes WHERE customer_id=?", (body["customer_id"],)) if body.get("customer_id") else None
    ped = R._pedido(body["order_id"]) if body.get("order_id") else None
    rid = str(900000000 + secrets.randbelow(99999999))
    row = {"reclamacao_id": rid, "origem": "simulada", "conversation_id": body.get("conversation_id") or f"RA-{rid}",
           "customer_id": body.get("customer_id") or (ped or {}).get("customer_id"), "order_id": body.get("order_id"),
           "empresa": body.get("empresa") or (ped or {}).get("marca") or "Gocase", "titulo": body["titulo"].strip(),
           "texto": body["texto"].strip(),
           "cidade": body.get("cidade") or (ped or {}).get("cidade_entrega") or (cli or {}).get("cidade"),
           "uf": body.get("uf") or (ped or {}).get("uf") or (cli or {}).get("uf"),
           "data_publicacao": R._iso(R._agora()), "case_id": None, "importado_em": None}
    R._insert("sim_ra_novas", row)
    res = {**row, "status_publico": "Não respondida", "idempotente": False}
    R._log(request, "reclame_aqui", "simular_nova", body, 200, res, None, key)
    await bus.publish("ra.nova", {"reclamacao_id": rid, "titulo": row["titulo"], "origem": "simulada"}, agent="reclame_aqui")
    return res


@api.post("/importar-fila")
async def post_importar_fila(request: Request, limite: int = Query(5, ge=1, le=100)):
    """Enfileira para o Goxy reclamações reais que estão em `fila_aberta` (canal reclame_aqui)."""
    garantir_tabelas()
    rows = R._q("""SELECT CAST(r.reclamacao_id AS TEXT) AS rid, r.conversation_id FROM reclame_aqui r
                   JOIN fila_aberta f ON f.conversation_id = r.conversation_id AND f.canal = 'reclame_aqui'
                   WHERE CAST(r.reclamacao_id AS TEXT) NOT IN (SELECT reclamacao_id FROM sim_ra_novas)
                   ORDER BY f.data_abertura LIMIT ?""", (limite,))
    for r in rows:
        R._insert("sim_ra_novas", {"reclamacao_id": r["rid"], "origem": "fila", "conversation_id": r["conversation_id"]})
    res = {"enfileiradas": [r["rid"] for r in rows], "total": len(rows)}
    R._log(request, "reclame_aqui", "importar_fila", {"limite": limite}, 200, res)
    if rows:
        await bus.publish("ra.nova", {"reclamacao_ids": res["enfileiradas"], "origem": "fila"}, agent="reclame_aqui")
    return res


# ------------------------------------------------------------------ portal público (HTML)

portal = APIRouter(prefix="/reclameaqui", tags=["Portal Reclame Aqui (simulado)"], include_in_schema=False)

_CSS = """
:root{--tinta:#1d1b3a;--suave:#5c5a7a;--fundo:#f4f2fb;--card:#fff;--borda:#dcd8ee;--acento:#5b3cc4;--acento2:#ff7a45;
 --ok:#1f8a5b;--mal:#c2410c;--raio:12px}
*{box-sizing:border-box}body{margin:0;background:var(--fundo);color:var(--tinta);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acento)}header{background:var(--tinta);color:#fff;padding:14px 16px}
header .w{max-width:980px;margin:0 auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
header b{font-size:20px;letter-spacing:-.5px}header b span{color:var(--acento2)}header small{color:#b9b5d8}
main{max-width:980px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--borda);border-radius:var(--raio);padding:16px;margin-bottom:14px}
.empresa{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.avatar{width:64px;height:64px;border-radius:16px;background:var(--acento);color:#fff;display:grid;place-items:center;font-size:28px;font-weight:800}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:12px}
.kpi{background:var(--fundo);border-radius:10px;padding:10px}.kpi b{display:block;font-size:22px}.kpi span{color:var(--suave);font-size:12px}
.selo-rep{display:inline-block;padding:4px 10px;border-radius:999px;background:var(--acento2);color:#fff;font-weight:700}
.lista{list-style:none;margin:0;padding:0}.lista li{border-top:1px solid var(--borda);padding:12px 0}
.lista li:first-child{border-top:0}.lista a.t{font-weight:700;text-decoration:none;color:var(--tinta)}
.meta{color:var(--suave);font-size:13px}
.st{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--borda);margin-left:6px}
.st-Resolvido{background:#e3f5ec;color:var(--ok);border-color:#b6e2cb}.st-Não{background:#fdecdf;color:var(--mal);border-color:#f7c9ab}
.st-Respondida{background:#ece7fb;color:var(--acento);border-color:#d3c9f5}
.msg{border-left:4px solid var(--borda);padding:8px 12px;margin:10px 0;background:var(--fundo);border-radius:0 10px 10px 0}
.msg.empresa{border-color:var(--acento)}.msg.goxys{border-color:var(--ok);background:#effaf4}
.selo{display:inline-block;background:var(--ok);color:#fff;font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px}
.filtros a{margin-right:10px;font-size:14px}nav.marcas a{margin-right:10px;font-size:13px}
footer{color:var(--suave);font-size:12px;text-align:center;padding:20px}
"""


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _data(v: Optional[str]) -> str:
    d = R._parse(v)
    return d.strftime("%d/%m/%Y %H:%M") if d else "-"


def _st(s: str) -> str:
    return f'<span class="st st-{_e((s or "").split(" ")[0])}">{_e(s)}</span>'


def _pagina(titulo: str, corpo: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(titulo)} - Reclame Aqui (simulado)</title>
<style>{_CSS}</style></head><body>
<header><div class="w"><b>Reclame<span>.</span>Aqui <small>(simulado)</small></b>
<small>Site de reclamações fictício para a demo do Goxy. Não é o site oficial.</small></div></header>
<main>{corpo}</main><footer>Reclame Aqui (simulado) &middot; dados do hackathon GoHacks &middot; ambiente de demonstração</footer>
</body></html>""")


@portal.get("", response_class=HTMLResponse)
def portal_empresa(empresa: str = "Gocase", status: str = "", pagina: int = Query(1, ge=1)):
    rep = reputacao(empresa)
    recs = [r for r in todas() if (r["empresa"] or "").lower() == empresa.lower()]
    if status:
        recs = [r for r in recs if r["status_publico"] == status]
    por = 30
    tot_pag = max(1, (len(recs) + por - 1) // por)
    itens = "".join(
        f'<li><a class="t" href="/reclameaqui/reclamacao/{_e(r["reclamacao_id"])}">{_e(r["titulo"])}</a>{_st(r["status_publico"])}'
        f'{" <span class=selo>Respondida pela empresa</span>" if r["respondida_por_goxys"] else ""}'
        f'<div class="meta">{_e(r["cidade"])} - {_e(r["uf"])} &middot; {_data(r["data_publicacao"])}</div>'
        f'<div>{_e((r["texto"] or "")[:180])}</div></li>'
        for r in recs[(pagina - 1) * por: pagina * por]) or "<li>Nenhuma reclamação.</li>"
    marcas = [m["empresa"] for m in R._q("SELECT DISTINCT empresa FROM reclame_aqui ORDER BY empresa")]
    filtros = "".join(f'<a href="?empresa={_e(empresa)}&status={_e(s)}">{_e(s or "Todas")}</a>'
                      for s in ("", "Não respondida", "Respondida", "Resolvido", "Não resolvido"))
    nav = "".join(f'<a href="?empresa={_e(m)}">{_e(m)}</a>' for m in marcas)
    pag = (f'<p class="meta">Página {pagina} de {tot_pag} '
           + (f'<a href="?empresa={_e(empresa)}&status={_e(status)}&pagina={pagina - 1}">anterior</a> ' if pagina > 1 else "")
           + (f'<a href="?empresa={_e(empresa)}&status={_e(status)}&pagina={pagina + 1}">próxima</a>' if pagina < tot_pag else "")
           + "</p>")
    corpo = f"""<nav class="marcas card">Empresas do grupo: {nav}</nav>
<section class="card" id="reputacao"><div class="empresa"><div class="avatar">{_e(empresa[:1])}</div>
<div><h1 style="margin:0">{_e(empresa)}</h1><span class="selo-rep" id="classificacao">Reputação {_e(rep["classificacao"])}</span>
<span class="meta"> índice <b id="indice">{rep["indice_reputacao"]}</b>/10</span></div></div>
<div class="kpis">
<div class="kpi"><b>{rep["total_reclamacoes"]}</b><span>reclamações</span></div>
<div class="kpi"><b id="pct-respondidas">{rep["pct_respondidas"]}%</b><span>respondidas</span></div>
<div class="kpi"><b>{rep["pct_resolvidas"]}%</b><span>resolvidas (das avaliadas)</span></div>
<div class="kpi"><b>{rep["pct_voltaria_a_fazer_negocio"]}%</b><span>voltariam a fazer negócio</span></div>
<div class="kpi"><b>{rep["nota_media_consumidor"] if rep["nota_media_consumidor"] is not None else "-"}</b><span>nota média do consumidor</span></div>
<div class="kpi"><b>{_e(rep["tempo_medio_primeira_resposta_txt"])}</b><span>tempo médio da 1ª resposta</span></div>
</div></section>
<section class="card"><h2 style="margin-top:0">Reclamações</h2><div class="filtros">{filtros}</div>
<ul class="lista" id="reclamacoes">{itens}</ul>{pag}</section>"""
    return _pagina(f"{empresa}: reputação e reclamações", corpo)


@portal.get("/reclamacao/{reclamacao_id}", response_class=HTMLResponse)
def portal_reclamacao(reclamacao_id: str):
    rec = uma(reclamacao_id)
    if not rec:
        r = _pagina("Não encontrada", "<div class='card'>Reclamação não encontrada.</div>")
        r.status_code = 404
        return r
    hist = ""
    for h in _historico(rec):
        if h["autor"] == "consumidor":
            hist += f'<div class="msg"><b>Consumidor</b> <span class="meta">{_data(h["data"])}</span><div>{_e(h["texto"])}</div></div>'
        else:
            cls = "msg empresa goxys" if h.get("selo") else "msg empresa"
            selo = f' <span class="selo">{_e(h["selo"])}</span>' if h.get("selo") else ""
            hist += (f'<div class="{cls}"><b>Resposta da empresa</b>{selo} <span class="meta">{_data(h["data"])}</span>'
                     f'<div>{_e(h["texto"])}</div></div>')
    corpo = f"""<p><a href="/reclameaqui?empresa={_e(rec["empresa"])}">&larr; {_e(rec["empresa"])}</a></p>
<article class="card" id="reclamacao" data-id="{_e(rec["reclamacao_id"])}"><h1 style="margin-top:0">{_e(rec["titulo"])}{_st(rec["status_publico"])}</h1>
<div class="meta">{_e(rec["empresa"])} &middot; {_e(rec["cidade"])} - {_e(rec["uf"])} &middot; {_data(rec["data_publicacao"])} &middot; ID {_e(rec["reclamacao_id"])}</div>
<p>{_e(rec["texto"])}</p></article>
<section class="card" id="historico"><h2 style="margin-top:0">Histórico público</h2>{hist or '<p class="meta">A empresa ainda não respondeu.</p>'}</section>"""
    return _pagina(rec["titulo"] or "Reclamação", corpo)


routers = [api, portal]
