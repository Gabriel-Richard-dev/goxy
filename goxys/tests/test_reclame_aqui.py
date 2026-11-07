"""Reclame Aqui simulado (CONTRACTS §9): API, portal e adaptador.

    cd goxys && .venv/bin/python -m pytest tests/test_reclame_aqui.py -q
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import Body, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from channels import reclame_aqui as adaptador  # noqa: E402
from data import build_db  # noqa: E402
from mock_api import routes  # noqa: E402

CASOS_CRIADOS: list[dict] = []
MENSAGENS_CASO: dict[str, list[dict]] = {}       # case_id -> case.mensagens (como a Voz grava)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    build_db.ensure()
    db = tmp_path_factory.mktemp("db") / "goxys.db"
    shutil.copy(build_db.config.DB_PATH, db)
    con = sqlite3.connect(db)
    for t in list(build_db.SIM_TABLES) + ["sim_ra_respostas", "sim_ra_novas"]:
        try:
            con.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()
    original = routes.DB_PATH
    routes.DB_PATH = db
    app = FastAPI()
    for r in routes.routers:
        app.include_router(r)

    @app.post("/api/goxys/casos")                 # stub do orquestrador
    def criar_caso(body: dict = Body(...)):
        CASOS_CRIADOS.append(body)
        return {"case_id": f"CASO-T{len(CASOS_CRIADOS)}"}

    @app.get("/api/goxys/casos/{case_id}")
    def get_caso(case_id: str):
        return {"id": case_id, "mensagens": MENSAGENS_CASO.get(case_id, [])}

    with TestClient(app) as c:
        async def api(method, path, json=None, timeout=20):
            u = urlsplit(path)
            r = c.request(method, u.path + (f"?{u.query}" if u.query else ""), json=json, headers={"X-Agente": "canal"})
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, r.text
        orig_api = adaptador._api
        adaptador._api = api
        yield c
        adaptador._api = orig_api
    routes.DB_PATH = original


def _uma_nao_respondida(client) -> dict:
    return client.get("/api/reclameaqui/reclamacoes", params={"status": "Não respondida", "empresa": "Gocase"}).json()[0]


def test_lista_vem_dos_dados_reais(client):
    r = client.get("/api/reclameaqui/reclamacoes")
    assert r.status_code == 200
    recs = r.json()
    assert len(recs) == 370
    com_texto = [x for x in recs if x["texto"]]
    assert len(com_texto) == 370 and not com_texto[0]["texto"].startswith("[")
    d = client.get("/api/reclameaqui/reclamacoes", params={"desde": "2026-11-01"}).json()
    assert d and all(x["data_publicacao"] >= "2026-11-01" for x in d)
    det = client.get(f"/api/reclameaqui/reclamacoes/{recs[0]['reclamacao_id']}").json()
    assert "historico_publico" in det
    assert client.get("/api/reclameaqui/reclamacoes/0").status_code == 404


def test_reputacao_calculada(client):
    rep = client.get("/api/reclameaqui/reputacao").json()
    con = sqlite3.connect(routes.DB_PATH)
    tot, resp, nota = con.execute("SELECT COUNT(*), COUNT(data_primeira_resposta_empresa), AVG(nota_consumidor) "
                                  "FROM reclame_aqui WHERE empresa='Gocase'").fetchone()
    con.close()
    assert rep["total_reclamacoes"] == tot
    assert rep["pct_respondidas"] == round(100 * resp / tot, 1)
    assert rep["nota_media_consumidor"] == round(nota, 2)
    for k in ("pct_resolvidas", "pct_voltaria_a_fazer_negocio", "tempo_medio_primeira_resposta_h", "indice_reputacao"):
        assert rep[k] is not None


def test_resposta_publica_sem_aprovacao_e_403(client):
    rid = _uma_nao_respondida(client)["reclamacao_id"]
    r = client.post(f"/api/reclameaqui/reclamacoes/{rid}/resposta-publica", json={"texto": "oi"})
    assert r.status_code == 403 and r.json()["erro"] == "exige_aprovacao"
    ap = client.post("/api/aprovacoes", json={"titulo": "x", "nivel": "C"}).json()
    r = client.post(f"/api/reclameaqui/reclamacoes/{rid}/resposta-publica",
                    json={"texto": "oi", "aprovacao_id": ap["aprovacao_id"]})
    assert r.status_code == 403                       # pendente não vale


def test_heuristica_sem_marca():
    ev = lambda t: {"type": "message.sent", "case_id": None, "data": {"canal": "reclame_aqui", "texto": t}}
    assert asyncio.run(adaptador.classificar(ev("Sua solicitação está em análise, te aviso aqui."))) == "privada"
    assert asyncio.run(adaptador.classificar(ev("O reenvio do seu copo já saiu, rastreio VL123."))) == "publica"
    assert asyncio.run(adaptador.classificar({"type": "message.sent", "data": {"situacao": "resolvido",
                                                                                "texto": "em análise"}})) == "publica"


def test_sanitizacao():
    t, rem = adaptador.sanitizar("CPF 123.456.789-09, cartão 4111 1111 1111 1111, tel (84) 99876-5432 "
                                 "e 11987654321. Pedido GBH361471 em 2026-11-02.")
    assert "123.456" not in t and "4111" not in t and "99876" not in t and "11987654321" not in t
    assert "GBH361471" in t and "2026-11-02" in t
    assert set(rem) == {"cpf", "cartao", "telefone"}


def test_fluxo_simular_aprovar_publicar(client):
    rep0 = client.get("/api/reclameaqui/reputacao").json()
    nova = client.post("/api/reclameaqui/simular-nova", json={
        "customer_id": "CH971787", "order_id": "GCH559588", "titulo": "Capinha chegou trincada",
        "texto": "Comprei a capinha e chegou trincada. Quero solução!"}).json()
    rid = nova["reclamacao_id"]
    criados = asyncio.run(adaptador.importar_pendentes())
    assert criados == [{"reclamacao_id": rid, "case_id": criados[0]["case_id"]}]
    assert CASOS_CRIADOS[-1]["canal"] == "reclame_aqui" and "Capinha chegou trincada" in CASOS_CRIADOS[-1]["mensagem"]
    assert asyncio.run(adaptador.importar_pendentes()) == []           # não importa 2x
    cid = criados[0]["case_id"]

    def enviar(texto, **marca):
        m = client.post("/api/mensagens", json={"canal": "reclame_aqui", "case_id": cid, "texto": texto}).json()
        MENSAGENS_CASO.setdefault(cid, []).append({"autor": "goxys", "texto": texto, "message_id": m["message_id"], **marca})
        return asyncio.run(adaptador.rotear_saida({"type": "message.sent", "case_id": cid, "data": {
            "canal": "reclame_aqui", "texto": texto, "message_id": m["message_id"]}}))

    n_aprov = len(client.get("/api/aprovacoes").json())
    # confirmação e status: só mensagem privada, sem aprovação
    r1 = enviar("Oi! Recebi sua mensagem e já estou cuidando do seu pedido.", tipo="confirmacao")
    r2 = enviar("Passando pra te atualizar: continua em análise.", situacao="sla")
    assert r1["tipo"] == r2["tipo"] == "privada" and r1["privada"] and r2["privada"]
    assert len(client.get("/api/aprovacoes").json()) == n_aprov
    # resposta final da Voz: pede aprovação
    adaptador.PENDENTES.clear()
    r3 = enviar("Sentimos muito! Já enviamos outra. Tel 84 99876-5432", situacao="resolvido")
    assert r3["tipo"] == "publica"
    aid = r3["aprovacao_id"]
    ap = client.get(f"/api/aprovacoes/{aid}").json()
    assert ap["titulo"] == "Publicar resposta pública no Reclame Aqui" and ap["nivel"] == "C" and "99876" not in ap["resumo"]
    assert client.get(f"/api/reclameaqui/reclamacoes/{rid}").json()["respostas_publicas"] == 0   # nada publicado ainda

    client.post(f"/api/aprovacoes/{aid}/decisao", json={"decisao": "ajustar", "aprovador": "ana", "canal": "painel",
                                                        "ajuste": {"nota": "Sentimos muito! O reenvio já saiu."}})
    adaptador.PENDENTES.clear()                       # força leitura pela API (como depois de um restart)
    res = asyncio.run(adaptador.ao_decidir({"type": "approval.decided", "case_id": cid,
                                            "data": {"aprovacao_id": aid, "decisao": "ajustar",
                                                     "ajuste": {"nota": "Sentimos muito! O reenvio já saiu."}}}))
    assert res["publicada"] and res["privada"]
    det = client.get(f"/api/reclameaqui/reclamacoes/{rid}").json()
    assert det["status_publico"] == "Respondida"
    assert det["historico_publico"][-1]["texto"] == "Sentimos muito! O reenvio já saiu."
    assert len(det["mensagens_privadas"]) == 3 and "GCH559588" in det["mensagens_privadas"][-1]["texto"]
    html = client.get(f"/reclameaqui/reclamacao/{rid}").text
    assert "Respondida pela empresa" in html and "O reenvio já saiu" in html and "Reclame Aqui (simulado)" in html
    rep1 = client.get("/api/reclameaqui/reputacao").json()
    assert rep1["total_reclamacoes"] == rep0["total_reclamacoes"] + 1 and rep1["respostas_goxys"] == 1
    assert "Gocase" in client.get("/reclameaqui").text


def test_negada_nao_publica(client):
    rid = _uma_nao_respondida(client)["reclamacao_id"]
    adaptador.VINCULOS["CASO-NEG"] = rid
    aid = asyncio.run(adaptador.pedir_aprovacao({"type": "message.sent", "case_id": "CASO-NEG",
                                                 "data": {"canal": "reclame_aqui", "texto": "texto"}}))
    client.post(f"/api/aprovacoes/{aid}/decisao", json={"decisao": "negar", "aprovador": "ana"})
    res = asyncio.run(adaptador.ao_decidir({"type": "approval.decided", "data": {"aprovacao_id": aid, "decisao": "negar"}}))
    assert res == {"publicada": False}
    assert client.get(f"/api/reclameaqui/reclamacoes/{rid}").json()["respostas_publicas"] == 0


def test_importar_fila(client):
    r = client.post("/api/reclameaqui/importar-fila", params={"limite": 5}).json()
    assert r["total"] == 5
    criados = asyncio.run(adaptador.importar_pendentes())
    assert {c["reclamacao_id"] for c in criados} == set(r["enfileiradas"])
