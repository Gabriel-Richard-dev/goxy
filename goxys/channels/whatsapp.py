"""WhatsApp do cliente via Twilio (sandbox).

Entrada: webhook POST /api/canais/whatsapp (form-urlencoded da Twilio) com `X-Twilio-Signature` validada
(HMAC-SHA1 do URL público + parâmetros ordenados, com o Auth Token). Responde TwiML vazio na hora e processa em
background: número sem caso aberto → caso novo (canal "whatsapp", `whatsapp_from`); número com caso aberto → a mensagem
entra nesse caso (POST /api/goxys/casos/{id}/cliente). Fotos são baixadas para assets/uploads/ (sem descrição inventada).

Saída: `message.sent` com canal "whatsapp" → REST da Twilio (Messages.json). Basic auth com a API Key (SK…) se houver;
senão Account SID + Auth Token. Erro de envio nunca derruba nada: log + evento `channel.error`.

Aprovações continuam no Slack, no Telegram e no painel: o WhatsApp é o canal do CLIENTE. O token nunca é logado.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import mimetypes
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from core import config
from core.bus import bus

from . import comum
from . import formatters as fmt

log = logging.getLogger("goxys.whatsapp")

router = APIRouter(prefix="/api/canais", tags=["Canais"])
TWIML_VAZIO = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
API = "https://api.twilio.com/2010-04-01"
UPLOADS = config.ASSETS_DIR / "uploads"
ABERTOS = ("novo", "em_andamento", "aguardando_cliente", "aguardando_aprovacao", "humano")

_inscrito = False
_tasks: set[asyncio.Task] = set()
_transport: httpx.AsyncBaseTransport | None = None      # os testes injetam um MockTransport


def configurado() -> bool:
    return bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_WHATSAPP_FROM)


def _auth() -> tuple[str, str]:
    if config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET:
        return config.TWILIO_API_KEY_SID, config.TWILIO_API_KEY_SECRET
    return config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _cli(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=kw.pop("timeout", 15), **kw)


# ------------------------------------------------------------------ assinatura

def assinatura(url: str, params: dict, token: str) -> str:
    """X-Twilio-Signature: base64(HMAC-SHA1(token, url + chave1valor1chave2valor2… em ordem alfabética))."""
    dados = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), dados.encode(), hashlib.sha1).digest()).decode()


def _urls_candidatas(request: Request) -> list[str]:
    """Atrás do Caddy o URL que a Twilio assinou é o público: GOXYS_PUBLIC_URL + path (+ query)."""
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    urls = [config.PUBLIC_URL.rstrip("/") + path]
    proto, host = request.headers.get("x-forwarded-proto"), request.headers.get("x-forwarded-host") or request.headers.get("host")
    if proto and host:
        urls.append(f"{proto}://{host}{path}")
    urls.append(str(request.url))
    return list(dict.fromkeys(urls))


def assinatura_valida(request: Request, params: dict) -> bool:
    recebida = request.headers.get("x-twilio-signature") or ""
    if not recebida or not config.TWILIO_AUTH_TOKEN:
        return False
    return any(hmac.compare_digest(assinatura(u, params, config.TWILIO_AUTH_TOKEN), recebida)
               for u in _urls_candidatas(request))


# ------------------------------------------------------------------ entrada

@router.post("/whatsapp", include_in_schema=False)
async def webhook(request: Request) -> Response:
    if not configurado():
        return Response("canal WhatsApp desligado", status_code=503)
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not assinatura_valida(request, params):
        log.warning("webhook WhatsApp com assinatura inválida (de %s)", params.get("From", "?")[:20])
        return Response("assinatura inválida", status_code=403)
    _bg(processar(params))                       # responde já (< 1 s); o caso corre em background
    return Response(TWIML_VAZIO, media_type="application/xml")


def caso_aberto(whatsapp_from: str) -> str | None:
    """Um caso aberto por número: a conversa continua nele até ser resolvido/rejeitado."""
    try:
        from core.state import store
        for c in store.all():
            if c.whatsapp_from == whatsapp_from and c.status in ABERTOS:
                return c.id
    except Exception:
        pass
    return None


async def _baixar_midia(params: dict, i: int) -> dict | None:
    url, tipo = params.get(f"MediaUrl{i}"), params.get(f"MediaContentType{i}") or ""
    if not url or not tipo.startswith("image/"):
        return None
    sid = params.get("MessageSid") or datetime.now().strftime("%H%M%S")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(
        tipo, (mimetypes.guess_extension(tipo) or ".jpg").lstrip("."))
    nome = f"wa_{sid}_{i}.{ext}"
    try:
        async with _cli(follow_redirects=True, timeout=30) as cli:
            r = await cli.get(url, auth=_auth())
        r.raise_for_status()
        UPLOADS.mkdir(parents=True, exist_ok=True)
        (UPLOADS / nome).write_bytes(r.content)
    except Exception as e:
        log.warning("não baixei a mídia %s do WhatsApp: %s", i, type(e).__name__)
        return None
    return {"anexo_id": f"WA-{sid}-{i}", "tipo": "foto_produto", "url": f"/assets-goxys/uploads/{nome}",
            "enviado_em": datetime.now().isoformat(timespec="seconds")}


async def processar(params: dict) -> dict:
    """Mensagem recebida → caso novo ou mensagem no caso aberto do mesmo número."""
    wa_from = params.get("From") or ""
    texto = (params.get("Body") or "").strip()
    anexos: list[dict] = []
    outras: list[str] = []
    for i in range(int(params.get("NumMedia") or 0)):
        a = await _baixar_midia(params, i)
        if a:
            anexos.append(a)
        elif params.get(f"MediaContentType{i}"):
            outras.append(params[f"MediaContentType{i}"])
    if outras and not texto:
        texto = f"[mídia recebida: {', '.join(outras)}]"
    try:
        cid = caso_aberto(wa_from)
        if cid:
            st, r = await comum.api("POST", f"/api/goxys/casos/{cid}/cliente", {"texto": texto, "anexos": anexos})
            return {"acao": "anexada", "case_id": cid, "status": st}
        body: dict = {"canal": "whatsapp", "mensagem": texto or "[foto anexada]", "whatsapp_from": wa_from,
                      "conversation_id": f"WA-{params.get('WaId') or wa_from[-8:]}-{(params.get('MessageSid') or '')[-8:]}"}
        if params.get("ProfileName"):
            body["cliente_nome"] = params["ProfileName"]
        if anexos:
            body["anexos"] = anexos
        pedido = fmt.extrair_pedido(texto)
        if pedido:
            body["order_id"] = pedido
            st, p = await comum.api("GET", f"/api/erp/pedidos/{pedido}")
            if st == 200 and isinstance(p, dict) and p.get("customer_id"):
                body["customer_id"] = p["customer_id"]
        st, r = await comum.api("POST", "/api/goxys/casos", body)
        if st >= 400:
            raise RuntimeError(f"HTTP {st}")
        log.info("caso %s aberto pelo WhatsApp", (r or {}).get("case_id"))
        return {"acao": "criada", "case_id": (r or {}).get("case_id"), "status": st}
    except Exception as e:
        log.exception("falha ao registrar mensagem do WhatsApp")
        await enviar(wa_from, "Tive um problema para registrar sua mensagem. Já avisei o nosso time.")
        return {"acao": "erro", "erro": str(e)}


# ------------------------------------------------------------------ saída

async def enviar(para: str, texto: str, case_id: str | None = None) -> dict:
    if not configurado() or not para or not texto:
        return {"ok": False, "erro": "desligado_ou_vazio"}
    to = para if para.startswith("whatsapp:") else f"whatsapp:{para}"
    url = f"{API}/Accounts/{config.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        async with _cli() as cli:
            r = await cli.post(url, data={"From": config.TWILIO_WHATSAPP_FROM, "To": to, "Body": texto[:1600]},
                               auth=_auth())
        corpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            msg = corpo.get("message") or r.text[:200]
            log.warning("Twilio recusou o envio (%s, código %s): %s", r.status_code, corpo.get("code"), msg)
            await bus.publish("channel.error", {"canal": "whatsapp", "status_http": r.status_code,
                                                "codigo": corpo.get("code"), "erro": msg}, case_id=case_id, agent="whatsapp")
            return {"ok": False, "status_http": r.status_code, "erro": msg}
        return {"ok": True, "sid": corpo.get("sid"), "status": corpo.get("status")}
    except httpx.HTTPError as e:
        log.warning("falha de rede ao enviar WhatsApp: %s", type(e).__name__)
        await bus.publish("channel.error", {"canal": "whatsapp", "erro": type(e).__name__}, case_id=case_id,
                          agent="whatsapp")
        return {"ok": False, "erro": type(e).__name__}


async def _on_evento(evt: dict) -> None:
    if evt.get("type") != "message.sent" or not configurado():
        return
    d = evt.get("data") or {}
    if d.get("canal") != "whatsapp" or not d.get("texto"):
        return
    para = d.get("whatsapp_to")
    if not para:
        try:
            from core.state import store
            c = store.get(evt.get("case_id") or "")
            para = c.whatsapp_from if c else None
        except Exception:
            para = None
    if para:                                   # casos de demo no canal "whatsapp" não têm número: não envia
        await enviar(para, d["texto"], evt.get("case_id"))


async def start() -> None:
    global _inscrito
    if not configurado():
        raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN/WHATSAPP_FROM ausentes")
    if not _inscrito:
        bus.subscribe_listener(_on_evento)
        _inscrito = True
    via = "API Key" if config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET else "Account SID"
    log.info("canal WhatsApp ligado (Twilio, de %s, REST via %s; webhook %s/api/canais/whatsapp)",
             config.TWILIO_WHATSAPP_FROM, via, config.PUBLIC_URL.rstrip("/"))


async def stop() -> None:
    for t in list(_tasks):
        t.cancel()
