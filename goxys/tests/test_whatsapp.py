"""Canal WhatsApp (Twilio): assinatura do webhook, caso a partir do form, mensagem em caso aberto, mídia e envio.

    cd goxys && .venv/bin/python -m pytest tests/test_whatsapp.py -q
"""
from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from channels import comum  # noqa: E402
from channels import whatsapp as wa  # noqa: E402
from core import config  # noqa: E402
from core.bus import bus  # noqa: E402
from core.state import Case, store  # noqa: E402

PUBLICO = "https://goxy.exemplo.dev"
TOKEN = "token-de-teste-123"
URL = f"{PUBLICO}/api/canais/whatsapp"


@pytest.fixture
def twilio(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC_teste")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setattr(config, "TWILIO_API_KEY_SID", "SK_teste")
    monkeypatch.setattr(config, "TWILIO_API_KEY_SECRET", "segredo")
    monkeypatch.setattr(config, "PUBLIC_URL", PUBLICO)
    monkeypatch.setattr(wa, "UPLOADS", tmp_path / "uploads")
    chamadas: list[tuple] = []

    async def api(method, path, json=None, timeout=20):
        chamadas.append((method, path, json))
        if path.startswith("/api/erp/pedidos/"):
            return 200, {"order_id": path.rsplit("/", 1)[-1], "customer_id": "CH971787"}
        return 200, {"case_id": "GXTESTE-WA"}
    monkeypatch.setattr(comum, "api", api)
    app = FastAPI()
    app.include_router(wa.router)
    with TestClient(app) as c:
        yield c, chamadas
    wa._transport = None


def _form(**kw):
    base = {"From": "whatsapp:+5585999990000", "WaId": "5585999990000", "ProfileName": "Gabriel", "NumMedia": "0",
            "MessageSid": "SM1234567890abcdef", "Body": ""}
    base.update(kw)
    return base


def _post(client, form, assinatura=None):
    sig = assinatura if assinatura is not None else wa.assinatura(URL, form, TOKEN)
    return client.post("/api/canais/whatsapp", data=form, headers={"X-Twilio-Signature": sig})


def _esperar(chamadas, n=1, timeout=3.0):
    t0 = time.time()
    while len([c for c in chamadas if c[0] == "POST"]) < n and time.time() - t0 < timeout:
        time.sleep(0.02)


def test_assinatura_hmac_sha1():
    params = {"From": "whatsapp:+5585", "Body": "oi", "NumMedia": "0"}
    esperado = base64.b64encode(__import__("hmac").new(TOKEN.encode(), (URL + "Bodyoi" + "Fromwhatsapp:+5585" + "NumMedia0")
                                                       .encode(), "sha1").digest()).decode()
    assert wa.assinatura(URL, params, TOKEN) == esperado


def test_assinatura_invalida_403(twilio):
    client, chamadas = twilio
    r = _post(client, _form(Body="oi"), assinatura="assinatura-falsa")
    assert r.status_code == 403 and chamadas == []
    r = client.post("/api/canais/whatsapp", data=_form(Body="oi"))          # sem header
    assert r.status_code == 403


def test_mensagem_nova_cria_caso_com_pedido(twilio):
    client, chamadas = twilio
    t0 = time.perf_counter()
    r = _post(client, _form(Body="Oi! Meu copo chegou rachado, pedido GCH559588"))
    assert r.status_code == 200 and r.text.endswith("<Response/>") and time.perf_counter() - t0 < 1.0
    _esperar(chamadas)
    post = next(c for c in chamadas if c[:2] == ("POST", "/api/goxys/casos"))
    body = post[2]
    assert body["canal"] == "whatsapp" and body["whatsapp_from"] == "whatsapp:+5585999990000"
    assert body["order_id"] == "GCH559588" and body["customer_id"] == "CH971787" and body["cliente_nome"] == "Gabriel"
    assert "confirmacao" not in body                    # a confirmação vai pela REST, como as demais


def test_numero_com_caso_aberto_anexa_ao_caso(twilio):
    client, chamadas = twilio
    c = Case(id="GXWA0001", canal="whatsapp", whatsapp_from="whatsapp:+5585999990000", status="aguardando_cliente")
    store._cases[c.id] = c
    try:
        _post(client, _form(Body="é o GCH559588"))
        _esperar(chamadas)
        assert ("POST", "/api/goxys/casos/GXWA0001/cliente", {"texto": "é o GCH559588", "anexos": []}) in chamadas
    finally:
        store._cases.pop(c.id, None)


def test_midia_baixada_com_auth_sem_descricao_inventada(twilio):
    client, chamadas = twilio
    vistos = {}

    def handler(req: httpx.Request) -> httpx.Response:
        vistos["auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=b"\xff\xd8\xff fake jpeg", headers={"content-type": "image/jpeg"})
    wa._transport = httpx.MockTransport(handler)
    _post(client, _form(Body="olha a foto", NumMedia="1", MediaUrl0="https://api.twilio.com/media/ME1",
                        MediaContentType0="image/jpeg"))
    _esperar(chamadas)
    body = next(c for c in chamadas if c[:2] == ("POST", "/api/goxys/casos"))[2]
    a = body["anexos"][0]
    assert a["url"].startswith("/assets-goxys/uploads/wa_") and a["url"].endswith(".jpg") and "descricao_visual" not in a
    assert vistos["auth"] == "Basic " + base64.b64encode(b"SK_teste:segredo").decode()     # API Key, não o token
    assert list(wa.UPLOADS.glob("wa_*.jpg"))


def test_envio_pela_rest_e_erro_sem_derrubar(twilio):
    pedidos = []

    def ok(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        return httpx.Response(201, json={"sid": "SMxyz", "status": "queued"})
    wa._transport = httpx.MockTransport(ok)
    r = asyncio.run(wa.enviar("whatsapp:+5585999990000", "Recebi sua mensagem!"))
    assert r == {"ok": True, "sid": "SMxyz", "status": "queued"}
    req = pedidos[0]
    assert str(req.url) == "https://api.twilio.com/2010-04-01/Accounts/AC_teste/Messages.json"
    form = parse_qs(req.content.decode())
    assert form == {"From": ["whatsapp:+14155238886"], "To": ["whatsapp:+5585999990000"], "Body": ["Recebi sua mensagem!"]}
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"SK_teste:segredo").decode()

    # sem API Key: cai no Account SID + Auth Token
    config.TWILIO_API_KEY_SID = ""
    asyncio.run(wa.enviar("+5585999990000", "oi"))
    assert pedidos[-1].headers["authorization"] == "Basic " + base64.b64encode(f"AC_teste:{TOKEN}".encode()).decode()
    assert parse_qs(pedidos[-1].content.decode())["To"] == ["whatsapp:+5585999990000"]

    # erro da Twilio (ex.: número fora do sandbox): devolve ok False e publica channel.error
    wa._transport = httpx.MockTransport(lambda req: httpx.Response(400, json={"code": 63015, "message": "not in sandbox"}))
    antes = len(bus.history())
    r = asyncio.run(wa.enviar("whatsapp:+5500000000000", "oi", "GXWA0002"))
    assert r["ok"] is False and r["status_http"] == 400
    ev = [e for e in bus.history()[antes:] if e["type"] == "channel.error"]
    assert ev and ev[0]["data"]["codigo"] == 63015 and TOKEN not in str(ev[0])


def test_message_sent_do_caso_whatsapp_vai_para_o_numero(twilio):
    pedidos = []
    wa._transport = httpx.MockTransport(lambda req: pedidos.append(req) or httpx.Response(201, json={"sid": "SM1"}))
    c = Case(id="GXWA0003", canal="whatsapp", whatsapp_from="whatsapp:+5585911112222")
    store._cases[c.id] = c
    try:
        asyncio.run(wa._on_evento({"type": "message.sent", "case_id": c.id, "data": {"canal": "whatsapp", "texto": "Oi!"}}))
        asyncio.run(wa._on_evento({"type": "message.sent", "case_id": "DEMO", "data": {"canal": "whatsapp", "texto": "x"}}))
        asyncio.run(wa._on_evento({"type": "message.sent", "case_id": c.id, "data": {"canal": "email", "texto": "x"}}))
    finally:
        store._cases.pop(c.id, None)
    assert len(pedidos) == 1 and parse_qs(pedidos[0].content.decode())["To"] == ["whatsapp:+5585911112222"]


def test_canal_desligado_responde_503(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "")
    app = FastAPI()
    app.include_router(wa.router)
    with TestClient(app) as c:
        assert c.post("/api/canais/whatsapp", data={"Body": "oi"}).status_code == 503
