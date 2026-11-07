"""Canal Slack (slack_bolt AsyncApp + Socket Mode; nada de URL pública).

- `approval.requested` → Block Kit no SLACK_APPROVALS_CHANNEL com resumo, evidências, nível, valor e botões.
- Aprovar/Negar → POST /api/aprovacoes/{id}/decisao (canal "slack"); Ajustar abre modal (valor + nota).
- `approval.decided` / `approval.escalated` → atualiza a mensagem (vale para decisões de outros canais).
- `policy.decided` com nível B → aviso ao supervisor com botão "Desfazer (15 min)".
- Slash `/goxys [status|demo n|insights]`.
"""
from __future__ import annotations

import logging
import time

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from core import config
from core.bus import bus

from . import comum
from . import formatters as fmt

log = logging.getLogger("goxys.slack")

DESFAZER_S = 15 * 60

_app: AsyncApp | None = None
_handler: AsyncSocketModeHandler | None = None
_inscrito = False
_postadas: dict[str, tuple[str, str]] = {}      # aprovacao_id -> (channel, ts)
_nivel_b: dict[str, float] = {}                  # case_id -> momento do aviso


def _painel(case_id: str | None) -> str | None:
    return f"{config.PUBLIC_URL}/app/?caso={case_id}" if case_id else None


async def _nome_usuario(client, user_id: str) -> str:
    """Nome + @handle do Slack: identifica a pessoa sem colidir com nomes da equipe da base (alçada/segregação)."""
    try:
        r = await client.users_info(user=user_id)
        u = r["user"]
        real, handle = u.get("real_name"), u.get("name")
        if real and handle:
            return f"{real} @{handle}"
        return real or (f"@{handle}" if handle else user_id)
    except Exception:
        return user_id


ENCERRADO_BLOCKS = [{"type": "section", "text": {"type": "mrkdwn",
                                                 "text": ":no_entry: *Pedido encerrado* — " + comum.TXT_ENCERRADO}}]


async def _aplicar_resultado(client, aid: str, r: dict, channel: str, ts: str) -> None:
    """404 → "Pedido encerrado"; 409 → resultado de quem decidiu; 403/erro → mantém os botões."""
    try:
        if r["tipo"] == "encerrado":
            _postadas.pop(aid, None)
            await client.chat_update(channel=channel, ts=ts, text="Pedido encerrado", blocks=ENCERRADO_BLOCKS)
        elif r["tipo"] == "decidido":
            _postadas.pop(aid, None)
            info = comum.APROVACOES.get(aid, {})
            pedido = info.get("pedido") or r.get("aprovacao") or {"aprovacao_id": aid}
            await client.chat_update(channel=channel, ts=ts, text=fmt.decisao_resumo(r["decidido"]),
                                     blocks=fmt.slack_decisao_blocks(pedido, r["decidido"], info.get("case_id")))
    except Exception:
        log.exception("não consegui atualizar a mensagem da aprovação %s", aid)


def _registrar(app: AsyncApp) -> None:
    async def _decidir_botao(ack, body, client, decisao: str):
        await ack()
        aid = body["actions"][0]["value"]
        quem = await _nome_usuario(client, body["user"]["id"])
        st, resp = await comum.decidir(aid, decisao, quem, "slack")
        r = comum.explicar_decisao(st, resp)
        ch, ts = body["channel"]["id"], body["message"]["ts"]
        if r["tipo"] != "ok":
            await client.chat_postEphemeral(channel=ch, user=body["user"]["id"], text=r["toast"])
            await _aplicar_resultado(client, aid, r, ch, ts)
        else:
            _postadas.setdefault(aid, (ch, ts))

    @app.action("gx_aprovar")
    async def aprovar(ack, body, client):
        await _decidir_botao(ack, body, client, "aprovar")

    @app.action("gx_negar")
    async def negar(ack, body, client):
        await _decidir_botao(ack, body, client, "negar")

    @app.action("gx_ajustar")
    async def ajustar(ack, body, client):
        await ack()
        aid = body["actions"][0]["value"]
        _postadas.setdefault(aid, (body["channel"]["id"], body["message"]["ts"]))
        pedido = await comum.pedido_aprovacao(aid)
        await client.views_open(trigger_id=body["trigger_id"], view=fmt.slack_ajuste_modal(aid, pedido))

    @app.view("gx_ajuste")
    async def ajuste_enviado(ack, body, client, view):
        aid = view["private_metadata"]
        ajuste = fmt.slack_ajuste_de_view(view["state"]["values"])
        if not ajuste:
            await ack(response_action="errors", errors={"valor": "Informe um valor ou uma nota."})
            return
        await ack()
        quem = await _nome_usuario(client, body["user"]["id"])
        st, resp = await comum.decidir(aid, "ajustar", quem, "slack", ajuste)
        r = comum.explicar_decisao(st, resp)
        if r["tipo"] != "ok":
            await client.chat_postMessage(channel=body["user"]["id"], text=r["toast"])
            if aid in _postadas:
                await _aplicar_resultado(client, aid, r, *_postadas[aid])

    @app.action("gx_desfazer")
    async def desfazer(ack, body, client, respond):
        await ack()
        case_id = body["actions"][0]["value"]
        quem = await _nome_usuario(client, body["user"]["id"])
        if time.time() - _nivel_b.get(case_id, 0) > DESFAZER_S:
            await respond(replace_original=False, response_type="ephemeral",
                          text="A janela de 15 min para desfazer já passou. Trate pelo painel.")
            return
        st, _ = await comum.api("POST", f"/api/goxys/casos/{case_id}/desfazer", {"por": quem, "canal": "slack"})
        if st < 400:
            txt = f":leftwards_arrow_with_hook: Desfeito por {quem}. O caso `{case_id}` voltou para revisão."
        else:
            txt = (f":leftwards_arrow_with_hook: {quem} pediu para desfazer o caso `{case_id}`; "
                   "reverta a ação no painel (rota de desfazer indisponível).")
        await respond(replace_original=True, text=txt,
                      blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": txt}}])

    @app.command("/goxys")
    async def comando(ack, command, respond):
        await ack()
        partes = (command.get("text") or "").split()
        sub = partes[0].lower() if partes else "status"
        if sub == "demo" and len(partes) > 1 and partes[1].isdigit():
            st, r = await comum.api("POST", f"/api/goxys/simular/{partes[1]}")
            await respond(f"Demo {partes[1]} disparada: caso `{r.get('case_id') if isinstance(r, dict) else '?'}`"
                          if st < 400 else f"Falhou ({st}).")
        elif sub == "insights":
            st, r = await comum.api("GET", "/api/goxys/analista")
            cl = (r or {}).get("clusters", [])[:5] if isinstance(r, dict) else []
            linhas = [f"• *{c['titulo']}* — {c['tamanho']} pedidos, {fmt.brl(c['custo_estimado_rs'])}, "
                      f"{c['contatos_evitaveis']} contatos evitáveis\n   _{c['acao_preventiva']}_" for c in cl]
            await respond("\n".join(linhas) or "Sem insights agora.")
        else:
            st, m = await comum.api("GET", "/api/goxys/metricas")
            if st >= 400 or not isinstance(m, dict):
                await respond("Métricas indisponíveis.")
                return
            await respond(f"*Goxy* · {m.get('total', 0)} casos · {m.get('autonomos_pct', 0)}% autônomos · "
                          f"{m.get('aprovacoes_pendentes', 0)} aprovações pendentes · "
                          f"fraude evitada {fmt.brl(m.get('fraude_evitada_rs', 0))}\n"
                          "Use `/goxys demo 1..5` ou `/goxys insights`.")


async def _on_evento(evt: dict) -> None:
    if _app is None or not config.SLACK_APPROVALS_CHANNEL:
        return
    t = evt.get("type")
    d = evt.get("data") or {}
    cli = _app.client
    canal = config.SLACK_APPROVALS_CHANNEL
    if t == "approval.requested":
        comum.lembrar_aprovacao(evt)
        aid = str(d.get("aprovacao_id") or "")
        if not aid:
            return
        r = await cli.chat_postMessage(channel=canal, text=fmt.slack_texto_fallback(d),
                                       blocks=fmt.slack_aprovacao_blocks(d, evt.get("case_id"), _painel(evt.get("case_id"))))
        _postadas[aid] = (r["channel"], r["ts"])
    elif t == "demo.reset":
        postadas = list(_postadas.items())
        _postadas.clear()
        _nivel_b.clear()
        comum.APROVACOES.clear()
        blocks = [{"type": "section", "text": {"type": "mrkdwn",
                                               "text": ":arrows_counterclockwise: *Demo reiniciada* — este pedido de aprovação foi encerrado."}}]
        for _aid, (ch, ts) in postadas:
            try:
                await cli.chat_update(channel=ch, ts=ts, text="Demo reiniciada", blocks=blocks)
            except Exception:
                log.info("não editei a mensagem %s no Slack", ts)
    elif t in ("approval.decided", "approval.escalated"):
        aid = str(d.get("aprovacao_id") or "")
        if aid not in _postadas:
            return
        ch, ts = _postadas[aid]
        info = comum.APROVACOES.get(aid, {})
        pedido = info.get("pedido") or {"aprovacao_id": aid}
        cid = info.get("case_id") or evt.get("case_id")
        if t == "approval.decided":
            blocks = fmt.slack_decisao_blocks(pedido, d, cid)
            texto = fmt.decisao_resumo(d)
        else:
            blocks = fmt.slack_escalado_blocks(pedido, cid)
            texto = "Escalado ao supervisor"
            await cli.chat_postMessage(channel=ch, thread_ts=ts, reply_broadcast=True,
                                       text=":rotating_light: Ninguém respondeu no SLA. Supervisor, sua decisão é necessária.")
        await cli.chat_update(channel=ch, ts=ts, text=texto, blocks=blocks)
    elif t == "policy.decided" and str(d.get("nivel")) == "B" and evt.get("case_id"):
        cid = evt["case_id"]
        if cid in _nivel_b:
            return
        _nivel_b[cid] = time.time()
        await cli.chat_postMessage(channel=canal, text=f"Nível B executado no caso {cid}",
                                   blocks=fmt.slack_nivel_b_blocks(cid, d))


async def start() -> None:
    global _app, _handler, _inscrito
    app = AsyncApp(token=config.SLACK_BOT_TOKEN)
    quem = await app.client.auth_test()    # falha rápido com token inválido
    _registrar(app)
    handler = AsyncSocketModeHandler(app, config.SLACK_APP_TOKEN)
    _handler = handler
    await handler.connect_async()          # não bloqueia (start_async bloquearia o lifespan)
    _app = app
    if not _inscrito:
        bus.subscribe_listener(_on_evento)
        _inscrito = True
    log.info("canal Slack ligado como %s (Socket Mode; aprovações em %s)", quem.get("user"),
             config.SLACK_APPROVALS_CHANNEL or "canal não configurado")


async def stop() -> None:
    global _app, _handler
    h, _handler, _app = _handler, None, None
    if h is not None:
        try:
            await h.close_async()
        except Exception:
            log.exception("erro ao desligar o Slack")
