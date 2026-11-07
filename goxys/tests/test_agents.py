"""Goxy de ponta a ponta: os 5 casos de demo contra o servidor de verdade (uvicorn numa thread, HTTP real
entre agentes e sistemas simulados), numa cópia do banco.

    cd goxys && .venv/bin/python -m pytest tests/test_agents.py -q            # sem IA e, se houver chave, com IA
    GOXYS_TEST_SEM_IA=1 .venv/bin/python -m pytest tests/test_agents.py -q    # só o fallback determinístico

Confere nível, ação e status de `data/demo_cases.json`, aprova o caso 4 pela mesma rota dos canais e vê o
fluxo retomar; também as travas de política nas ferramentas e a idempotência do reembolso.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

CASOS = json.loads((ROOT / "data" / "demo_cases.json").read_text(encoding="utf-8"))
CHAVE_IA = config.AI_PROXY_API_KEY
FINAIS = ("resolvido", "rejeitado", "humano", "aguardando_aprovacao", "erro")


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    from data import build_db
    build_db.ensure()
    db = tmp_path_factory.mktemp("db") / "goxys.db"
    shutil.copy(config.DB_PATH, db)
    con = sqlite3.connect(db)
    for t in build_db.SIM_TABLES:
        con.execute(f"DELETE FROM {t}")
    con.execute("CREATE TABLE IF NOT EXISTS goxys_cases (id TEXT PRIMARY KEY, snapshot TEXT, atualizado_em TEXT)")
    con.execute("DELETE FROM goxys_cases")          # não recarregar casos do banco real
    con.commit()
    con.close()
    import agents.grafo
    agents.grafo.CHECKPOINTS = db.parent / "checkpoints.db"
    porta = _porta_livre()
    url = f"http://127.0.0.1:{porta}"

    import core.state
    import mock_api.routes as routes
    originais = (config.DB_PATH, core.state.DB_PATH, routes.DB_PATH, config.SYSTEMS_BASE_URL,
                 config.DEMO_STEP_DELAY_S, config.APPROVAL_SLA_S)
    config.DB_PATH = core.state.DB_PATH = routes.DB_PATH = db
    config.SYSTEMS_BASE_URL = url
    config.DEMO_STEP_DELAY_S = 0
    config.APPROVAL_SLA_S = 600

    from core.bus import bus
    ouvintes = list(bus._listeners)                 # o lifespan registra ouvintes globais; devolvidos no fim
    import uvicorn
    import app as app_mod
    server = uvicorn.Server(uvicorn.Config(app_mod.app, host="127.0.0.1", port=porta, log_level="warning",
                                           lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "servidor não subiu"
    yield url
    server.should_exit = True
    th.join(10)
    bus._listeners[:] = ouvintes
    import agents.maestro
    agents.maestro._registrado = False
    (config.DB_PATH, core.state.DB_PATH, routes.DB_PATH, config.SYSTEMS_BASE_URL,
     config.DEMO_STEP_DELAY_S, config.APPROVAL_SLA_S) = originais
    config.AI_PROXY_API_KEY = CHAVE_IA


def _esperar(url: str, ids: dict[int, str], finais=FINAIS, timeout: float = 150) -> dict[int, dict]:
    t0 = time.time()
    while True:
        cs = {n: httpx.get(f"{url}/api/goxys/casos/{i}", timeout=10).json() for n, i in ids.items()}
        if all(c["status"] in finais for c in cs.values()):
            return cs
        if time.time() - t0 > timeout:
            raise AssertionError({n: c["status"] for n, c in cs.items()})
        time.sleep(0.5)


VARIANTES = ["sem_ia"] + ([] if (os.getenv("GOXYS_TEST_SEM_IA") or not CHAVE_IA) else ["com_ia"])


@pytest.mark.parametrize("variante", VARIANTES)
def test_cinco_casos_ponta_a_ponta(base, variante):
    config.AI_PROXY_API_KEY = CHAVE_IA if variante == "com_ia" else ""
    ids = {}
    for c in CASOS:
        r = httpx.post(f"{base}/api/goxys/simular/{c['n']}", timeout=10)
        assert r.status_code == 200, r.text
        ids[c["n"]] = r.json()["case_id"]
    cs = _esperar(base, ids)

    for c in CASOS:
        caso, esp = cs[c["n"]], c["esperado"]
        tag = f"[{variante}] caso {c['n']} ({c['titulo']})"
        assert caso["decisao"].get("nivel") == esp["nivel"], (tag, caso["decisao"].get("motivo"))
        assert caso["decisao"].get("acao") == esp["acao"], (tag, caso["decisao"].get("acao"))
        assert caso["status"] == esp["status_final"], (tag, caso["status"], caso["decisao"].get("erro"))
        # auditável: justificativa, sinais e versão da política em toda decisão
        assert caso["decisao"].get("politica_versao") and caso["decisao"].get("motivo"), tag
        assert "custo_erro" in caso["decisao"]["sinais"], tag
        # o cliente sempre recebe resposta, sem CPF/cartão/telefone
        assert caso["resposta"].get("message_id"), (tag, caso["resposta"])
        # linha do tempo viva: nenhum passo ficou rodando
        assert not [s for s in caso["timeline"] if s["status"] == "rodando"], tag

    # caso 1: reenvio executado no nível A
    c1 = cs[1]
    assert c1["decisao"]["execucao"]["ok"] and c1["veracidade"]["score"] > 80
    # caso 2: Detetive desmonta a foto — reuso, comparação lado a lado, nada executado, sem acusar o cliente
    c2 = cs[2]
    assert c2["veracidade"]["score"] < 40 and c2["veracidade"]["fraude_objetiva"]
    comp = c2["veracidade"]["comparacao"]
    assert comp and comp["original"]["anexo_id"] and comp["atual"]["anexo_id"] == CASOS[1]["anexos"][0]["anexo_id"]
    assert comp["outros_usos"] and comp["outros_usos"][0]["anexo_id"] == comp["original"]["anexo_id"]
    assert not [a for a in c2["acoes"] if a["tipo"] == "escrita_critica" or a["ferramenta"] == "create_exchange"]
    assert "fraud" not in c2["resposta"]["texto"].lower()
    # caso 3: API 501 → agente de navegador no portal → chamado na transportadora
    c3 = cs[3]
    assert c3["dossie"]["fonte_rastreio"] == "portal"
    ferr = [(a["ferramenta"], a["status_http"]) for a in c3["acoes"]]
    assert ("get_tracking", 501) in ferr and ("portal_tracking", 200) in ferr and ("open_carrier_ticket", 200) in ferr
    assert any("portal" in s["titulo"] for s in c3["timeline"] if s["agent"] == "rastreador")
    # caso 5: congelado, nenhuma escrita além do chamado ao analista sênior e da mensagem
    c5 = cs[5]
    assert c5["decisao"].get("congelado")
    assert not [a for a in c5["acoes"] if a["ferramenta"] in ("create_exchange", "issue_refund", "open_carrier_ticket")]

    # caso 4: aprovação pela mesma rota dos canais → o fluxo retoma e conclui
    c4 = cs[4]
    ap = c4["aprovacao"]["aprovacao_id"]
    assert ap and not [a for a in c4["acoes"] if a["ferramenta"] == "issue_refund"]
    r = httpx.post(f"{base}/api/aprovacoes/{ap}/decisao",
                   json={"decisao": "aprovar", "aprovador": "teste", "canal": "painel"}, timeout=10)
    assert r.status_code == 200, r.text
    c4 = _esperar(base, {4: ids[4]}, finais=("resolvido", "rejeitado", "erro"))[4]
    assert c4["status"] == "resolvido", c4["decisao"].get("execucao")
    assert c4["aprovacao"]["decisao"] == "aprovar" and c4["aprovacao"]["status"] == "aprovada"
    reemb = [a for a in c4["acoes"] if a["ferramenta"] == "issue_refund"]
    assert len(reemb) == 1 and reemb[0]["status_http"] == 200 and reemb[0]["parametros"].get("aprovacao_id") == ap
    # decidir de novo: 409, e o caso não roda duas vezes
    r = httpx.post(f"{base}/api/aprovacoes/{ap}/decisao", json={"decisao": "negar"}, timeout=10)
    assert r.status_code == 409

    m = httpx.get(f"{base}/api/goxys/metricas", timeout=30).json()
    assert m["total"] >= 5 and m["fraudes_barradas"] >= 1 and m["baseline"]["tempo_medio_humano_min"]


def test_ferramentas_revalidam_politica(base):
    """Defesa em profundidade: a ferramenta recusa escrita sem nível permitido; reembolso é idempotente."""
    import tools
    from core.state import Case

    async def go():
        c = Case(id="GXTESTE001", order_id=CASOS[0]["order_id"], customer_id=CASOS[0]["customer_id"])
        for nivel in (None, "D"):
            with pytest.raises(tools.PolicyViolation):
                await tools.issue_refund(c, c.order_id, 50, "teste", nivel_autonomia=nivel)
        with pytest.raises(tools.PolicyViolation):  # C sem aprovação registrada
            await tools.create_exchange(c, c.order_id, None, "reenvio", "teste", nivel_autonomia="C")
        c.decisao = {"congelado": True}
        with pytest.raises(tools.PolicyViolation):
            await tools.open_carrier_ticket(c, c.order_id, "teste", nivel_autonomia="A")
        c.decisao = {}
        r1 = await tools.issue_refund(c, c.order_id, 10, "teste", nivel_autonomia="A")
        r2 = await tools.issue_refund(c, c.order_id, 10, "teste", nivel_autonomia="A")
        assert r1["reembolso_id"] == r2["reembolso_id"] and r2.get("idempotente")
        # > R$ 500 sem aprovação: o financeiro recusa com 403 mesmo se a política fosse burlada
        import api
        api._reset_demo(CASOS[3]["order_id"])  # o teste anterior já reembolsou este pedido
        c2 = Case(id="GXTESTE002", order_id=CASOS[3]["order_id"])
        with pytest.raises(tools.ToolError) as e:
            await tools.issue_refund(c2, c2.order_id, 600, "teste", nivel_autonomia="B")
        assert e.value.status == 403

    asyncio.run(go())


def test_politica_custo_assimetrico():
    """Faixa amarela de valor baixo: agir e avisar (B) custa menos que travar o cliente honesto."""
    from agents import politica
    from core.state import Case

    def caso(valor, score, juridico=False, fraude=False):
        return Case(id="X", anexos=[{"anexo_id": "a"}],
                    triagem={"intencao": "avaria", "risco_juridico": juridico, "confianca": 0.9},
                    dossie={"pedido": {"valor": valor, "sku": "S"}, "cliente": {"receita_acumulada": 300}},
                    veracidade={"score": score, "fraude_objetiva": fraude})

    assert politica.decide(caso(60, 95))["nivel"] == "A"
    assert politica.decide(caso(180, 90))["nivel"] == "B"            # acima de 150, dentro da alçada de 200
    assert politica.decide(caso(250, 90))["nivel"] == "C"            # acima da alçada do atendente
    assert politica.decide(caso(30, 60))["nivel"] == "B"             # custo assimétrico
    assert politica.decide(caso(150, 55))["nivel"] == "C"            # amarelo e caro: pede aprovação
    assert politica.decide(caso(60, 20))["nivel"] == "C"             # suspeita sem prova objetiva: nunca nega sozinho
    d = politica.decide(caso(60, 20, fraude=True))
    assert d["nivel"] == "D" and d["acao"] == "negar"
    assert politica.decide(caso(10, 99, juridico=True))["nivel"] == "D"
    assert politica.decide(caso(60, 95))["politica_versao"] == politica.versao()


# ------------------------------------------------------------------ humano no caso, grafo e limpeza

def _simular(base, n):
    r = httpx.post(f"{base}/api/goxys/simular/{n}", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["case_id"]


def _caso(base, cid):
    return httpx.get(f"{base}/api/goxys/casos/{cid}", timeout=10).json()


def test_passar_conversar_devolver(base):
    config.AI_PROXY_API_KEY = ""
    equipe = httpx.get(f"{base}/api/goxys/equipe", timeout=10).json()
    assert equipe and {"colaborador_id", "nome", "cargo", "nivel", "canais_atendidos", "supervisor_id", "status"} <= set(equipe[0])
    assert all(p["status"] == "ativo" for p in equipe)

    # caso 5 (D): sugere analista; passa; conversa; devolve "seguir" com nota de aprovação → executa
    cid = _simular(base, 5)
    c = _esperar(base, {5: cid})[5]
    assert c["status"] == "humano" and c["sugestao_responsavel"]["cargo"] == "analista"
    assert len(c["decisao"]["porque"]) <= 140
    para = c["sugestao_responsavel"]["colaborador_id"]
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/passar", json={"para": para, "nota": "cliente VIP"}, timeout=30)
    assert r.status_code == 200, r.text
    c = _caso(base, cid)
    assert c["status"] == "humano" and c["responsavel"]["colaborador_id"] == para
    assert len(c["resumo_passagem"].splitlines()) == 3
    assert any(s["titulo"].startswith("Maestro passou o caso para") for s in c["timeline"])
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/sugerir-resposta", timeout=30)
    assert r.status_code == 200 and r.json()["texto"]
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/mensagens", json={"texto": "Oi, Rodrigo! Vou cuidar disso.", "autor": para},
                   timeout=10)
    assert r.status_code == 200, r.text
    httpx.post(f"{base}/api/goxys/casos/{cid}/cliente", json={"texto": "ok, aguardo"}, timeout=10)
    c = _caso(base, cid)
    autores = [m["autor"] for m in c["mensagens"]]
    assert autores[0] == "cliente" and c["mensagens"][1].get("tipo") == "confirmacao"
    assert autores[-2:] == ["agente", "cliente"] and "goxys" in autores
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/devolver",
                   json={"nota": "Falei com ele. Pode fazer o reembolso.", "instrucao": "seguir"}, timeout=10)
    assert r.status_code == 200, r.text
    c = _esperar(base, {5: cid}, finais=("resolvido", "rejeitado", "erro"))[5]
    assert c["status"] == "resolvido" and c["responsavel"] is None, c["decisao"].get("execucao")
    assert c["decisao"]["acao"] == "reembolso" and c["decisao"].get("aprovado_por_humano")
    assert c["aprovacao"]["decisao"] == "aprovar" and c["aprovacao"]["via"] == "devolucao"
    assert any(a["ferramenta"] == "issue_refund" and a["status_http"] == 200 for a in c["acoes"])
    assert any(s["titulo"].endswith("devolveu o caso") for s in c["timeline"])

    # caso 4 (C) passado a uma pessoa: a aprovação pendente continua válida, mas nada executa até devolver
    cid = _simular(base, 4)
    c = _esperar(base, {4: cid})[4]
    assert c["sugestao_responsavel"]["cargo"] == "supervisor"
    assert "R$ 689,70" in c["aprovacao"]["titulo"] and c["aprovacao"]["titulo"].count("R$") == 1
    httpx.post(f"{base}/api/goxys/casos/{cid}/passar", json={"para": "atendente_01"}, timeout=30)
    ap = c["aprovacao"]["aprovacao_id"]
    httpx.post(f"{base}/api/aprovacoes/{ap}/decisao", json={"decisao": "aprovar", "aprovador": "sup", "canal": "slack"},
               timeout=10)
    time.sleep(1.0)
    c = _caso(base, cid)
    assert c["status"] == "humano" and c["aprovacao"]["decisao"] == "aprovar"
    assert not any(a["ferramenta"] == "issue_refund" for a in c["acoes"])
    httpx.post(f"{base}/api/goxys/casos/{cid}/devolver", json={"nota": "ok", "instrucao": "seguir"}, timeout=10)
    c = _esperar(base, {4: cid}, finais=("resolvido", "rejeitado", "erro"))[4]
    assert c["status"] == "resolvido" and any(a["ferramenta"] == "issue_refund" and a["status_http"] == 200
                                              for a in c["acoes"])

    # devolver "encerrar"
    cid = _simular(base, 2)
    _esperar(base, {2: cid})
    httpx.post(f"{base}/api/goxys/casos/{cid}/passar", json={"para": "supervisor_01"}, timeout=30)
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/devolver", json={"nota": "confirmado", "instrucao": "encerrar"}, timeout=10)
    assert r.status_code == 200 and _caso(base, cid)["status"] == "resolvido"


def _persona(nome: str) -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("sint_t", ROOT / "scripts" / "clientes_sinteticos.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = next(x for x in m.personas(m.Base()) if x["persona"] == nome)
    return {k: p[k] for k in ("canal", "mensagem", "customer_id", "order_id", "anexos") if p.get(k)}


def test_sla_escala_e_avisa_uma_vez(base):
    config.AI_PROXY_API_KEY = ""
    assert httpx.post(f"{base}/api/goxys/demo/sla", json={"segundos": 2}, timeout=10).json()["sla_s"] == 2
    try:
        cid = _simular(base, 4)
        c = _esperar(base, {4: cid})[4]
        assert c["status"] == "aguardando_aprovacao"
        for _ in range(60):
            c = _caso(base, cid)
            if (c["aprovacao"] or {}).get("escalada_para") and any(m.get("situacao") == "sla" for m in c["mensagens"]):
                break
            time.sleep(0.25)
        esc = c["aprovacao"]["escalada_para"]
        assert esc["colaborador_id"] and esc["nome"]
        assert esc["colaborador_id"] != (c["sugestao_responsavel"] or {}).get("colaborador_id")
        assert any(s["titulo"] == f"Mensageiro escalou para {esc['nome']}" for s in c["timeline"])
        time.sleep(2.5)                                      # não avisa de novo
        c = _caso(base, cid)
        assert sum(1 for m in c["mensagens"] if m.get("situacao") == "sla") == 1
        assert "supervisor" in next(m["texto"] for m in c["mensagens"] if m.get("situacao") == "sla")
        r = httpx.post(f"{base}/api/aprovacoes/{c['aprovacao']['aprovacao_id']}/decisao",
                       json={"decisao": "aprovar", "aprovador": esc["colaborador_id"], "canal": "painel"}, timeout=10)
        assert r.status_code == 200, r.text                  # continua decidível depois de escalar
        c = _esperar(base, {4: cid}, finais=("resolvido", "erro", "rejeitado"))[4]
        assert c["status"] == "resolvido"
    finally:
        httpx.post(f"{base}/api/goxys/demo/sla", json={"segundos": 600}, timeout=10)


def test_ajustar_usa_valor_ajustado(base):
    config.AI_PROXY_API_KEY = ""
    cid = _simular(base, 4)
    c = _esperar(base, {4: cid})[4]
    ap = c["aprovacao"]["aprovacao_id"]
    assert c["aprovacao"]["recomendacao"]["decisao_sugerida"] in ("aprovar", "negar")
    r = httpx.post(f"{base}/api/aprovacoes/{ap}/decisao", timeout=10, json={
        "decisao": "ajustar", "aprovador": "supervisor_02", "canal": "painel",
        "ajuste": {"valor": 400, "nota": "Reembolso parcial: só um frasco vazou."}})
    assert r.status_code == 200, r.text
    assert r.json()["concordou"] is False
    c = _esperar(base, {4: cid}, finais=("resolvido", "erro", "rejeitado"))[4]
    assert c["status"] == "resolvido", c["decisao"].get("execucao")
    reemb = [a for a in c["acoes"] if a["ferramenta"] == "issue_refund"]
    assert len(reemb) == 1 and reemb[0]["parametros"]["valor"] == 400 and reemb[0]["parametros"]["aprovacao_id"] == ap
    assert reemb[0]["resultado"]["valor"] == 400
    assert "R$ 400,00" in c["resposta"]["texto"]
    assert c["decisao"]["valor_original"] == 689.7


def test_segregacao_e_alcada(base):
    config.AI_PROXY_API_KEY = ""
    cid = _simular(base, 4)
    c = _esperar(base, {4: cid})[4]
    ap = c["aprovacao"]["aprovacao_id"]
    url = f"{base}/api/aprovacoes/{ap}/decisao"
    r = httpx.post(url, json={"decisao": "aprovar", "aprovador": c["aprovacao"]["solicitante"]}, timeout=10)
    assert r.status_code == 403 and r.json()["erro"] == "segregacao" and "outra pessoa" in r.json()["mensagem"]
    r = httpx.post(url, json={"decisao": "aprovar", "aprovador": "atendente_02"}, timeout=10)
    assert r.status_code == 403 and r.json()["erro"] == "alcada" and "alçada" in r.json()["mensagem"]
    httpx.post(f"{base}/api/goxys/casos/{cid}/passar", json={"para": "supervisor_03"}, timeout=30)
    r = httpx.post(url, json={"decisao": "aprovar", "aprovador": "supervisor_03"}, timeout=10)
    assert r.status_code == 403 and r.json()["erro"] == "segregacao" and "está com você" in r.json()["mensagem"]
    r = httpx.post(url, json={"decisao": "negar", "aprovador": "supervisor_01"}, timeout=10)
    assert r.status_code == 200 and r.json()["concordou"] is False
    # devolução por atendente com "pode fazer o reembolso" de R$ 689,70: passa da alçada → fica pendente para outra pessoa
    cid = _simular(base, 4)
    c = _esperar(base, {4: cid})[4]
    httpx.post(f"{base}/api/goxys/casos/{cid}/passar", json={"para": "atendente_05"}, timeout=30)
    httpx.post(f"{base}/api/goxys/casos/{cid}/devolver", json={"nota": "pode fazer o reembolso", "instrucao": "seguir"},
               timeout=10)
    c = _esperar(base, {4: cid})[4]
    assert c["status"] == "aguardando_aprovacao" and not any(a["ferramenta"] == "issue_refund" for a in c["acoes"])


def test_desfazer_de_verdade(base):
    config.AI_PROXY_API_KEY = ""
    body = _persona("honesto_avaria_media")
    cid = httpx.post(f"{base}/api/goxys/casos", json=body, timeout=10).json()["case_id"]
    c = _esperar(base, {0: cid})[0]
    assert c["decisao"]["nivel"] == "B" and c["status"] == "resolvido" and c["decisao"]["desfazer_ate"]
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/desfazer", json={"por": "supervisor_01"}, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()["desfeito"]
    assert d["por"] == "supervisor_01" and d["acoes"] and d["acoes"][0]["status"] == "cancelado"
    c = _caso(base, cid)
    assert c["decisao"]["desfeito"] and any(s["titulo"].startswith("desfeito por") for s in c["timeline"])
    assert any(m.get("situacao") == "desfeito" for m in c["mensagens"])
    assert any(a["ferramenta"] == "cancel_exchange" and a["status_http"] == 200 for a in c["acoes"])
    r2 = httpx.post(f"{base}/api/goxys/casos/{cid}/desfazer", json={"por": "supervisor_01"}, timeout=30)
    assert r2.status_code == 200 and r2.json()["desfeito"]["ts"] == d["ts"]          # idempotente
    # fora da janela de 15 min → 409 prazo_expirado
    cid = httpx.post(f"{base}/api/goxys/casos", json=body, timeout=10).json()["case_id"]
    _esperar(base, {0: cid})
    from core.state import store
    store.get(cid).decisao["desfazer_ate"] = "2000-01-01T00:00:00"
    r = httpx.post(f"{base}/api/goxys/casos/{cid}/desfazer", json={"por": "supervisor_01"}, timeout=30)
    assert r.status_code == 409 and r.json()["erro"] == "prazo_expirado"


def test_aprendizado_sugestao_validar_aplicar(base, tmp_path):
    config.AI_PROXY_API_KEY = ""
    import agents.aprendizado as apr
    from agents import politica
    pol = tmp_path / "autonomia.yaml"
    shutil.copy(config.POLICY_PATH, pol)
    original_pol, original_arq = config.POLICY_PATH, apr._arquivo
    config.POLICY_PATH = pol
    apr._arquivo = lambda: tmp_path / "sugestoes.json"
    apr.SUGESTOES.clear()
    try:
        cid = _simular(base, 4)                               # uma decisão humana divergente na sessão
        c = _esperar(base, {4: cid})[4]
        httpx.post(f"{base}/api/aprovacoes/{c['aprovacao']['aprovacao_id']}/decisao", timeout=10,
                   json={"decisao": "ajustar", "aprovador": "supervisor_02", "ajuste": {"valor": 300}})
        _esperar(base, {4: cid}, finais=("resolvido", "erro", "rejeitado"))
        a = httpx.get(f"{base}/api/goxys/aprendizado", timeout=60).json()
        assert a["total_decisoes"] >= 1 and a["concordancia_pct"] is not None and a["divergencias"]
        assert any(h["fonte"] == "acoes_executadas_sistemas" for h in a["historico_real"])
        sug = next(s for s in a["sugestoes"] if s["id"] == "S-reenvio-200-300")
        assert sug["status"] == "proposta" and "de 45 reenvios" in sug["evidencia"] and "base real" in sug["evidencia"]
        r = httpx.post(f"{base}/api/goxys/aprendizado/sugestoes/{sug['id']}/aplicar", json={"por": "x"}, timeout=10)
        assert r.status_code == 409                          # não validada ainda
        job = httpx.post(f"{base}/api/goxys/aprendizado/sugestoes/{sug['id']}/validar", timeout=10).json()
        for _ in range(900):
            j = httpx.get(f"{base}/api/goxys/aprendizado/jobs/{job['job_id']}", timeout=10).json()
            if j["status"] != "rodando":
                break
            time.sleep(0.5)
        assert j["status"] == "concluido" and j["placar"] == {"acertos": 20, "total": 20}, j["erros"]
        assert j["sugestao"]["status"] == "validada"
        assert politica.versao().endswith("-r2")             # validar não mexe no YAML
        ids_fila = {c["id"] for c in httpx.get(f"{base}/api/goxys/casos", timeout=10).json()}
        assert not ids_fila & {e.get("case_id") for e in j.get("erros", [])}   # casos da validação fora da fila
        r = httpx.post(f"{base}/api/goxys/aprendizado/sugestoes/{sug['id']}/aplicar", json={"por": "supervisor_01"},
                       timeout=10)
        assert r.status_code == 200, r.text
        nova = r.json()["aplicada"]["versao"]
        assert nova.endswith("-r3") and politica.versao() == nova
        assert (tmp_path / "historico").glob("autonomia-*-r2.yaml")
        assert politica.carregar()["acoes"]["reenvio"]["limite_papel_rs"] == 300
        assert "limite_papel_rs: 300" in pol.read_text(encoding="utf-8")        # comentários preservados
        cid = _simular(base, 1)
        c = _esperar(base, {1: cid})[1]
        assert c["decisao"]["politica_versao"] == nova
    finally:
        config.POLICY_PATH, apr._arquivo = original_pol, original_arq
        politica._carregar.cache_clear()


def test_grafo_e_limpar_demo(base):
    g = httpx.get(f"{base}/api/goxys/grafo", timeout=10).json()
    assert "aprovacao" in g["mermaid"] and {n["id"] for n in g["nos"]} >= {"porteiro", "rastreador", "detetive_prep",
                                                                          "detetive", "politica", "aprovacao", "voz"}
    r = httpx.post(f"{base}/api/goxys/demo/limpar", timeout=30)
    assert r.status_code == 200 and r.json()["casos_apagados"] >= 1
    assert httpx.get(f"{base}/api/goxys/casos", timeout=10).json() == []
    assert httpx.get(f"{base}/api/aprovacoes?status=pendente", timeout=10).json() == []


def test_aprovacao_sobrevive_a_reinicio(tmp_path):
    """Caso 4 pendente → MATA o servidor (SIGKILL) → sobe de novo → aprova → conclui com reembolso."""
    import subprocess
    from data import build_db
    db = tmp_path / "goxys.db"
    shutil.copy(config.DB_PATH, db)
    con = sqlite3.connect(db)
    for t in build_db.SIM_TABLES:
        con.execute(f"DELETE FROM {t}")
    con.execute("CREATE TABLE IF NOT EXISTS goxys_cases (id TEXT PRIMARY KEY, snapshot TEXT, atualizado_em TEXT)")
    con.execute("DELETE FROM goxys_cases")
    con.commit()
    con.close()
    porta = _porta_livre()
    url = f"http://127.0.0.1:{porta}"
    env = {**os.environ, "GOXYS_DB": str(db), "GOXYS_CHECKPOINTS": str(tmp_path / "checkpoints.db"),
           "GOXYS_PORT": str(porta), "GOXYS_SYSTEMS_URL": url, "GOXYS_STEP_DELAY": "0", "AI_PROXY_API_KEY": "",
           "TELEGRAM_BOT_TOKEN": "", "SLACK_BOT_TOKEN": "", "SLACK_APP_TOKEN": ""}

    def subir():
        p = subprocess.Popen([str(ROOT / ".venv" / "bin" / "uvicorn"), "app:app", "--port", str(porta)], cwd=ROOT, env=env,
                             stdout=open(tmp_path / "srv.log", "ab"), stderr=subprocess.STDOUT)
        for _ in range(200):
            try:
                if httpx.get(f"{url}/api/goxys/saude", timeout=1).status_code == 200:
                    return p
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        p.kill()
        raise AssertionError((tmp_path / "srv.log").read_text()[-2000:])

    p = subir()
    try:
        cid = _simular(url, 4)
        c = _esperar(url, {4: cid})[4]
        assert c["status"] == "aguardando_aprovacao"
        ap = c["aprovacao"]["aprovacao_id"]
    finally:
        p.kill()
        p.wait(10)
    p = subir()
    try:
        c = _caso(url, cid)
        assert c["status"] == "aguardando_aprovacao", c.get("status")
        r = httpx.post(f"{url}/api/aprovacoes/{ap}/decisao", json={"decisao": "aprovar", "aprovador": "teste", "canal": "painel"},
                       timeout=10)
        assert r.status_code == 200, r.text
        c = _esperar(url, {4: cid}, finais=("resolvido", "rejeitado", "erro"))[4]
        assert c["status"] == "resolvido", c["decisao"].get("erro")
        reemb = [a for a in c["acoes"] if a["ferramenta"] == "issue_refund"]
        assert len(reemb) == 1 and reemb[0]["status_http"] == 200 and reemb[0]["parametros"].get("aprovacao_id") == ap
        nova = _simular(url, 1)                    # ids continuam únicos depois do reinício
        assert nova != cid
    finally:
        p.terminate()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()
            raise AssertionError("servidor não encerrou com SIGTERM (thread do checkpointer?)")
