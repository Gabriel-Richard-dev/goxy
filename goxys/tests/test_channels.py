"""Formatadores dos canais (Telegram/Slack) e insights do Analista."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from channels import formatters as fmt  # noqa: E402

PEDIDO = {
    "aprovacao_id": "APR0007",
    "titulo": "Reembolso de R$ 689,90 — foto duvidosa",
    "resumo": "Cliente pede reembolso integral; foto sem EXIF <script>",
    "nivel": "C",
    "valor": 689.9,
    "acao_proposta": "reembolso",
    "evidencias": ["EXIF ausente", {"tipo": "detetive", "texto": "score 55 (faixa cinzenta)"}, {"hash": "abc", "pedido": "GCH1"}],
    "expira_em": "2026-11-02T08:30:00",
}


def test_brl_e_pedido():
    assert fmt.brl(1234.5) == "R$ 1.234,50"
    assert fmt.brl(None) == "—"
    assert fmt.extrair_pedido("meu pedido gch527010 chegou quebrado") == "GCH527010"
    assert fmt.extrair_pedido("sem número") is None


@pytest.mark.parametrize("txt,esperado", [
    ("350", {"valor": 350.0}),
    ("R$ 1.250,50 cliente fica com o produto", {"valor": 1250.5, "nota": "cliente fica com o produto"}),
    ("199.9 - parcial", {"valor": 199.9, "nota": "parcial"}),
    ("só reenviar", {"nota": "só reenviar"}),
    ("", {}),
])
def test_parse_ajuste(txt, esperado):
    assert fmt.parse_ajuste(txt) == esperado


def test_evidencia_texto():
    assert fmt.evidencia_texto("x") == "x"
    assert fmt.evidencia_texto({"tipo": "detetive", "texto": "score 55"}) == "detetive: score 55"
    assert "hash: abc" in fmt.evidencia_texto({"hash": "abc", "pedido": "GCH1"})


def test_telegram_aprovacao_texto_escapa_e_resume():
    t = fmt.telegram_aprovacao_texto(PEDIDO, "GX0801001")
    assert "GX0801001" in t and "R$ 689,90" in t and "C · aprovação obrigatória" in t
    assert "&lt;script&gt;" in t and "<script>" not in t
    assert "• detetive: score 55 (faixa cinzenta)" in t
    assert "08:30" in t


def test_telegram_botoes_callback_roundtrip():
    linhas = fmt.telegram_aprovacao_botoes("APR0007")
    rotulos = [r for r, _ in linhas[0]]
    assert rotulos == ["✅ Aprovar", "⛔ Negar", "✏️ Ajustar"]
    for _, cb in linhas[0]:
        assert len(cb.encode()) <= 64
        assert fmt.parse_callback(cb)[1] == "APR0007"
    assert fmt.parse_callback("gx|apagar|X") is None
    assert fmt.parse_callback(None) is None


def test_telegram_decisao_remove_prazo_e_mostra_resultado():
    dec = {"aprovacao_id": "APR0007", "decisao": "ajustar", "aprovador": "Ana", "canal": "slack",
           "ajuste": {"valor": 350, "nota": "parcial"}, "tempo_resposta_s": 42.7}
    t = fmt.telegram_decisao_texto(PEDIDO, dec, "GX1")
    assert "⏱" not in t
    assert "Ajustado por Ana via slack em 42 s (novo valor R$ 350,00; nota: parcial)" in t
    assert "escalado ao supervisor" in fmt.telegram_escalado_texto(PEDIDO, "GX1")


def _tem(blocks, tipo):
    return [b for b in blocks if b["type"] == tipo]


def test_slack_aprovacao_blocks():
    b = fmt.slack_aprovacao_blocks(PEDIDO, "GX1", "http://x/app/?caso=GX1")
    json.dumps(b)  # serializável
    assert b[0]["type"] == "header" and len(b[0]["text"]["text"]) <= 150
    acoes = _tem(b, "actions")[0]["elements"]
    assert [e["action_id"] for e in acoes] == ["gx_aprovar", "gx_negar", "gx_ajustar"]
    assert all(e["value"] == "APR0007" for e in acoes)
    campos = " ".join(f["text"] for f in _tem(b, "section")[1]["fields"])
    assert "R$ 689,90" in campos and "C · aprovação obrigatória" in campos and "reembolso" in campos
    assert "&lt;script&gt;" in b[1]["text"]["text"]
    assert any("Evidências" in s["text"]["text"] for s in _tem(b, "section") if "text" in s)
    assert "abrir no painel" in _tem(b, "context")[0]["elements"][0]["text"]


def test_slack_decisao_e_escalado():
    dec = {"aprovacao_id": "APR0007", "decisao": "aprovar", "aprovador": "Bruno", "canal": "telegram"}
    b = fmt.slack_decisao_blocks(PEDIDO, dec, "GX1")
    assert not _tem(b, "actions")
    assert "Aprovado por Bruno via telegram" in b[-1]["text"]["text"]
    e = fmt.slack_escalado_blocks(PEDIDO, "GX1")
    assert _tem(e, "actions") and "supervisor" in e[1]["text"]["text"]


def test_slack_modal_ajuste():
    m = fmt.slack_ajuste_modal("APR0007", PEDIDO)
    assert m["callback_id"] == "gx_ajuste" and m["private_metadata"] == "APR0007"
    assert m["blocks"][1]["element"]["initial_value"] == "689,90"
    vals = {"valor": {"valor": {"value": "500,00"}}, "nota": {"nota": {"value": " teto "}}}
    assert fmt.slack_ajuste_de_view(vals) == {"valor": 500.0, "nota": "teto"}
    assert fmt.slack_ajuste_de_view({"valor": {"valor": {"value": None}}, "nota": {"nota": {"value": None}}}) == {}


def test_slack_nivel_b():
    b = fmt.slack_nivel_b_blocks("GX9", {"acao": "troca", "valor": 249.9, "motivo": "veracidade 85"})
    assert "nível B" in b[0]["text"]["text"] and "R$ 249,90" in b[0]["text"]["text"]
    btn = b[1]["elements"][0]
    assert btn["action_id"] == "gx_desfazer" and btn["value"] == "GX9" and "15 min" in btn["text"]["text"]


# ------------------------------------------------------------------ Analista

@pytest.fixture(scope="module")
def ins():
    from agents.analista import insights
    return insights()


def test_insights_estrutura(ins):
    assert ins["resumo"]["conversas"] > 1000
    tipos = {c["tipo"] for c in ins["clusters"]}
    assert {"transportadora_uf", "impressora", "lote", "cobranca_dobro"} <= tipos
    for c in ins["clusters"]:
        for k in ("id", "titulo", "tamanho", "custo_estimado_rs", "causa_provavel", "acao_preventiva", "contatos_evitaveis"):
            assert k in c, (c["id"], k)
        assert c["tamanho"] > 0 and c["custo_estimado_rs"] >= 0 and c["contatos_evitaveis"] >= 0
    assert ins["clusters"][0]["tipo"] == "fotos_reaproveitadas"  # auditoria retroativa vem primeiro
    custos = [c["custo_estimado_rs"] for c in ins["clusters"][1:]]
    assert custos == sorted(custos, reverse=True)


def test_insight_fotos_reaproveitadas(ins):
    f = ins["fotos_reaproveitadas"]
    assert f is ins["clusters"][0]
    assert (f["reusos"], f["pagos"], f["custo_rs"]) == (9, 9, 527.24)
    assert f["titulo"] == "9 fotos reaproveitadas foram pagas"
    assert f["acao"] == "Detetive teria barrado os 9 antes do reenvio"
    assert sum(len(c["reusos"]) for c in f["casos"]) == 9
    caso2 = next(c for c in f["casos"] if c["hash"] == "41b2c50cabf85942")
    assert caso2["original"]["anexo_id"] == "IMG000123"
    assert [r["anexo_id"] for r in caso2["reusos"]] == ["IMG000267"] and caso2["reusos"][0]["pago_rs"] == 45.05
    for c in f["casos"]:
        for r in c["reusos"]:
            assert r["customer_id"] != c["original"]["customer_id"]
            assert r["enviado_em"] > c["original"]["enviado_em"] and r["pago_rs"] > 0


def test_insights_dados_reais(ins):
    rotas = [c for c in ins["clusters"] if c["tipo"] == "transportadora_uf"]
    # o maior cluster logístico da base é a Expressa Já no Nordeste
    assert rotas[0]["chave"]["transportadora"] == "Expressa Já"
    assert rotas[0]["chave"]["uf"] in {"BA", "PE", "CE"}
    for r in rotas:
        assert r["alvo"]["notificar_hoje"] <= 300
    dobro = next(c for c in ins["clusters"] if c["tipo"] == "cobranca_dobro")
    assert dobro["tamanho"] >= 50 and dobro["ainda_sem_estorno"] >= 1


def test_insights_cache(ins):
    from agents.analista import insights
    assert insights() is ins
    assert insights(refresh=True) is not ins


def test_telegram_chat_id_dica():
    t = fmt.telegram_chat_id_dica(-100123)
    assert "<code>-100123</code>" in t and "TELEGRAM_APPROVERS_CHAT_ID" in t


# ------------------------------------------------------------------ botões antigos / demo reiniciada

from channels import comum  # noqa: E402


def test_explicar_decisao():
    assert comum.explicar_decisao(200, {})["tipo"] == "ok"
    r = comum.explicar_decisao(404, {"erro": "aprovacao_nao_encontrada"})
    assert r["tipo"] == "encerrado" and "demo foi reiniciada" in r["toast"] and not r["manter_botoes"]
    r = comum.explicar_decisao(409, {"erro": "ja_decidida", "aprovacao": {"aprovacao_id": "A1", "decisao": "aprovar",
                                                                         "aprovador": "Ana @ana", "canal": "slack"}})
    assert r["tipo"] == "decidido" and r["toast"] == "Já decidido por Ana @ana via slack" and r["decidido"]["decisao"] == "aprovar"
    r = comum.explicar_decisao(403, {"erro": "alcada", "mensagem": "Reembolso de R$ 689,70 passa da sua alçada"})
    assert r["tipo"] == "bloqueado" and r["toast"].startswith("Reembolso") and r["manter_botoes"]


def test_nome_telegram_nao_colide_com_a_equipe():
    from types import SimpleNamespace as U
    assert comum.nome_telegram(U(first_name="Camila", username="cami", id=1)) == "Camila @cami"
    assert comum.nome_telegram(U(first_name=None, username="cami", id=1)) == "@cami"
    assert comum.nome_telegram(U(first_name="Camila", username=None, id=7)) == "Camila (telegram 7)"


class _Bot:
    def __init__(self):
        self.edicoes = []

    async def edit_message_text(self, texto, chat_id=None, message_id=None, parse_mode=None, reply_markup=None):
        self.edicoes.append({"texto": texto, "chat_id": chat_id, "message_id": message_id, "teclado": reply_markup})


class _Query:
    def __init__(self, data, user):
        from types import SimpleNamespace as N
        self.data, self.from_user = data, user
        self.message = N(chat=N(id=-100), message_id=55)
        self.respostas = []

    async def answer(self, texto=None, show_alert=False):
        self.respostas.append(texto)


def _cb(aid):
    return next(c for linha in fmt.telegram_aprovacao_botoes(aid) for _, c in linha if fmt.parse_callback(c)[0] == "aprovar")


@pytest.mark.parametrize("st,body,tipo", [
    (404, {"erro": "aprovacao_nao_encontrada"}, "encerrado"),
    (409, {"erro": "ja_decidida", "aprovacao": {"aprovacao_id": "APR1", "decisao": "negar", "aprovador": "Bia", "canal": "painel"}},
     "decidido"),
    (403, {"erro": "segregacao", "mensagem": "Você pediu essa ação; outra pessoa precisa aprovar."}, "bloqueado"),
])
def test_telegram_botao_antigo(monkeypatch, st, body, tipo):
    import asyncio
    from types import SimpleNamespace as N
    from channels import telegram as tg
    bot = _Bot()
    monkeypatch.setattr(tg, "_app", N(bot=bot))
    vistos = {}

    async def decidir(aid, decisao, aprovador, canal, ajuste=None):
        vistos["aprovador"] = aprovador
        return st, body
    monkeypatch.setattr(comum, "decidir", decidir)
    tg._postadas["APR1"] = (-100, 55)
    q = _Query(_cb("APR1"), N(first_name="Gabriel", username="gabriel", id=9))
    asyncio.run(tg._callback(N(callback_query=q), None))
    assert vistos["aprovador"] == "Gabriel @gabriel"
    assert q.respostas == [comum.explicar_decisao(st, body)["toast"]]
    if tipo == "encerrado":
        assert "Pedido encerrado" in bot.edicoes[0]["texto"] and bot.edicoes[0]["teclado"] is None
        assert "APR1" not in tg._postadas
    elif tipo == "decidido":
        assert "Bia" in bot.edicoes[0]["texto"] and bot.edicoes[0]["teclado"] is None
    else:
        assert bot.edicoes == [] and "APR1" in tg._postadas          # botões continuam
    tg._postadas.clear()


def test_telegram_demo_reset_encerra_as_mensagens(monkeypatch):
    import asyncio
    from types import SimpleNamespace as N
    from channels import telegram as tg
    bot = _Bot()
    monkeypatch.setattr(tg, "_app", N(bot=bot))
    tg._postadas.update({"A1": (-100, 1), "A2": (-100, 2)})
    asyncio.run(tg._on_evento({"type": "demo.reset", "data": {}, "case_id": None}))
    assert len(bot.edicoes) == 2 and all("Demo reiniciada" in e["texto"] and e["teclado"] is None for e in bot.edicoes)
    assert tg._postadas == {}
