"""Sistemas simulados: as 3 travas da demo e a perícia de foto dos casos 1 e 2.

    cd goxys && .venv/bin/python -m pytest tests/test_mock_api.py -q
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import build_db  # noqa: E402
from mock_api import routes  # noqa: E402

CASOS = {c["n"]: c for c in json.loads((ROOT / "data" / "demo_cases.json").read_text(encoding="utf-8"))}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    build_db.ensure()                                   # banco "oficial" pronto
    db = tmp_path_factory.mktemp("db") / "goxys.db"
    shutil.copy(build_db.config.DB_PATH, db)            # os testes escrevem numa cópia
    con = sqlite3.connect(db)                           # parte de sistemas "zerados"
    for t in build_db.SIM_TABLES:
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
    original = routes.DB_PATH
    routes.DB_PATH = db
    app = FastAPI()
    for r in routes.routers:
        app.include_router(r)
    with TestClient(app) as c:
        yield c
    routes.DB_PATH = original


def _pericia(client, n):
    caso = CASOS[n]
    r = client.post("/api/erp/pericia-foto", json={"order_id": caso["order_id"], "customer_id": caso["customer_id"],
                                                   "anexo": caso["anexos"][0]})
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------- trava 1: R$ 500 sem aprovação → 403

def test_reembolso_acima_de_500_sem_aprovacao_e_403(client):
    r = client.post("/api/financeiro/reembolsos", json={"order_id": "GBH990004", "valor": 689.70, "motivo": "vazamento"},
                    headers={"Idempotency-Key": "t-403"})
    assert r.status_code == 403
    assert r.json()["erro"] == "exige_aprovacao"


def test_reembolso_com_aprovacao_pendente_ou_negada_continua_403(client):
    a = client.post("/api/aprovacoes", json={"case_id": "T", "titulo": "x", "nivel": "C", "valor": 689.70,
                                             "acao_proposta": "reembolso"}).json()
    r = client.post("/api/financeiro/reembolsos", json={"order_id": "GBH990004", "valor": 689.70, "motivo": "x",
                                                        "aprovacao_id": a["aprovacao_id"]})
    assert r.status_code == 403
    client.post(f"/api/aprovacoes/{a['aprovacao_id']}/decisao", json={"decisao": "negar", "aprovador": "s", "canal": "painel"})
    r = client.post("/api/financeiro/reembolsos", json={"order_id": "GBH990004", "valor": 689.70, "motivo": "x",
                                                        "aprovacao_id": a["aprovacao_id"]})
    assert r.status_code == 403


def test_aprovacao_decidida_libera_e_segunda_decisao_e_409(client):
    a = client.post("/api/aprovacoes", json={"case_id": "T2", "titulo": "Reembolso", "nivel": "C", "valor": 689.70,
                                             "acao_proposta": "reembolso"}).json()
    assert a["status"] == "pendente"
    pend = client.get("/api/aprovacoes", params={"status": "pendente"}).json()
    assert any(x["aprovacao_id"] == a["aprovacao_id"] for x in pend)
    d = client.post(f"/api/aprovacoes/{a['aprovacao_id']}/decisao",
                    json={"decisao": "aprovar", "aprovador": "supervisor_01", "canal": "telegram"})
    assert d.status_code == 200 and d.json()["status"] == "aprovada"
    assert d.json()["tempo_resposta_s"] is not None
    from core.bus import bus
    tipos = {(e["type"], e["data"].get("aprovacao_id")) for e in bus.history()}
    assert ("approval.requested", a["aprovacao_id"]) in tipos and ("approval.decided", a["aprovacao_id"]) in tipos
    d2 = client.post(f"/api/aprovacoes/{a['aprovacao_id']}/decisao",
                     json={"decisao": "negar", "aprovador": "outro", "canal": "slack"})
    assert d2.status_code == 409


# ---------------------------------------------------------------- trava 2: idempotência

def test_mesma_chave_nunca_paga_duas_vezes(client):
    a = client.post("/api/aprovacoes", json={"case_id": "T3", "titulo": "x", "nivel": "C", "valor": 689.70,
                                             "acao_proposta": "reembolso"}).json()
    client.post(f"/api/aprovacoes/{a['aprovacao_id']}/decisao",
                json={"decisao": "aprovar", "aprovador": "supervisor_01", "canal": "painel"})
    body = {"order_id": "GBH990004", "valor": 689.70, "motivo": "vazamento", "aprovacao_id": a["aprovacao_id"]}
    r1 = client.post("/api/financeiro/reembolsos", json=body, headers={"Idempotency-Key": "caso-x:reembolso"})
    r2 = client.post("/api/financeiro/reembolsos", json=body, headers={"Idempotency-Key": "caso-x:reembolso"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["reembolso_id"] == r2.json()["reembolso_id"]
    assert r1.json()["idempotente"] is False and r2.json()["idempotente"] is True
    linhas = routes._q("SELECT COUNT(*) n FROM sim_reembolsos WHERE idempotency_key='caso-x:reembolso'")
    assert linhas[0]["n"] == 1
    # e uma chave nova não reembolsa além do valor pago
    r3 = client.post("/api/financeiro/reembolsos", json=body, headers={"Idempotency-Key": "outra"})
    assert r3.status_code == 422


def test_reembolso_baixo_passa_sem_aprovacao(client):
    r = client.post("/api/financeiro/reembolsos", json={"order_id": "GCH559588", "valor": 50, "motivo": "teste"},
                    headers={"Idempotency-Key": "baixo"})
    assert r.status_code == 200 and r.json()["status"] == "aprovado"


# ---------------------------------------------------------------- trava 3: TransNorte sem API → 501 + portal

def test_transnorte_rastreio_501_e_portal(client):
    r = client.get("/api/erp/rastreio/GCH558661")
    assert r.status_code == 501
    assert r.json()["erro"] == "sem_api" and r.json()["portal"] == "/portal"
    # sem login o portal manda para a tela de login
    r = client.get("/portal/rastreio", params={"pedido": "GCH558661"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.post("/portal/login", data={"usuario": "goxys", "senha": "errada"}).status_code == 401
    r = client.post("/portal/login", data={"usuario": "goxys", "senha": "hackathon"})
    assert r.status_code == 200
    html = client.get("/portal/rastreio", params={"pedido": "GCH558661"}).text
    assert 'id="eventos"' in html and 'data-status="chegada_unidade_destino"' in html
    assert '<span id="previsao"' in html and "2026-10-30" in html


def test_rastreio_com_api_marca_atraso(client):
    r = client.get("/api/erp/rastreio/GCH547832").json()
    assert r["status"] == "extraviado" and r["atrasado"] is True and r["dias_atraso"] > 0
    assert r["eventos"][-1]["status"] == "extraviado"


# ---------------------------------------------------------------- perícia: casos 1 e 2

def test_pericia_caso1_foto_legitima(client):
    p = _pericia(client, 1)
    assert p["avaria_detectada"] is True and p["confianca"] >= 0.8
    assert p["reuso"] == []
    assert p["exif"]["presente"] and p["exif"]["antes_da_entrega"] is False and not p["exif"]["software_suspeito"]
    assert p["indicios_ia"] == [] and p["produto_confere"] is True
    assert p["score_veracidade"] > 80


def test_pericia_caso2_foto_reaproveitada(client):
    """Caso real AT114498: IMG000267 reusa o hash de IMG000123 (outro cliente, enviado antes)."""
    p = _pericia(client, 2)
    assert [r["anexo_id"] for r in p["reuso"]] == ["IMG000123"]  # só o anterior, nunca o próprio
    assert p["reuso"][0]["customer_id"] == "CH999406" != CASOS[2]["customer_id"]
    assert p["exif"]["antes_da_entrega"] is True and p["exif"]["software_suspeito"] is False
    assert p["produto_confere"] is False and "Xadrez Vichy" in p["motivo_produto"]
    assert p["score_veracidade"] < 40
    # a perícia do original não acusa reuso (nada foi enviado antes dele)
    orig = client.get("/api/erp/anexos", params={"hash": "41b2c50cabf85942"}).json()[0]
    r = client.post("/api/erp/pericia-foto", json={"order_id": orig["order_id"], "customer_id": orig["customer_id"],
                                                   "anexo": orig}).json()
    assert orig["anexo_id"] == "IMG000123" and r["reuso"] == []


def test_pericia_caso4_foto_duvidosa(client):
    p = _pericia(client, 4)
    assert p["exif"]["presente"] is False and p["reuso"] == []
    assert 40 <= p["score_veracidade"] <= 70


def test_cliente_mascara_cpf_e_flag_risco(client):
    c = client.get("/api/erp/clientes/CH116018").json()
    assert c["cpf"].startswith("***") and c["cpf"].endswith("-**")
    assert c["flag_risco"] is True
    assert client.get("/api/erp/clientes/CH971787").json()["flag_risco"] is False


def test_escritas_logadas_e_mensagem(client):
    r = client.post("/api/mensagens", json={"canal": "whatsapp", "customer_id": "CH971787",
                                            "conversation_id": "DEMO-AT001", "case_id": "TM", "texto": "Oi!"})
    assert r.status_code == 200
    assert client.get("/api/mensagens", params={"conversation_id": "DEMO-AT001"}).json()
    ev = client.get("/api/eventos", params={"case_id": "TM"}).json()
    assert ev and ev[-1]["acao"] == "enviar_mensagem"
