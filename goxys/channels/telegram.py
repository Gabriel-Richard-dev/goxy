"""Canal Telegram (python-telegram-bot v22, long polling dentro do loop do uvicorn).

Cliente → bot: texto/foto viram caso (POST /api/goxys/casos, canal "telegram") com confirmação imediata.
Bus → cliente: `message.sent` com telegram_chat_id é entregue no chat do cliente.
Aprovações: `approval.requested` vai ao chat TELEGRAM_APPROVERS_CHAT_ID com botões; o clique chama
POST /api/aprovacoes/{id}/decisao (canal "telegram"); `approval.decided`/`approval.escalated` editam a mensagem.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from telegram import (BotCommand, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import Conflict, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from core import config
from core.bus import bus

from . import comum
from . import formatters as fmt

log = logging.getLogger("goxys.telegram")

UPLOADS = config.ASSETS_DIR / "uploads"
JANELA_S = 2.5            # junta texto + fotos mandados em sequência num só caso

_app: Application | None = None
_inscrito = False
_postadas: dict[str, tuple[int, int]] = {}      # aprovacao_id -> (chat_id, message_id)
_prompts_ajuste: dict[int, str] = {}            # message_id do prompt -> aprovacao_id
_buffers: dict[int, dict] = {}                  # chat_id -> {"textos", "anexos", "task", "msg_id"}


def _teclado(aprovacao_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in linha]
                                 for linha in fmt.telegram_aprovacao_botoes(aprovacao_id)])


def _aprovadores() -> int | None:
    try:
        return int(config.TELEGRAM_APPROVERS_CHAT_ID) if config.TELEGRAM_APPROVERS_CHAT_ID else None
    except ValueError:
        return None


def _nome(user) -> str:
    if not user:
        return "telegram"
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


# ------------------------------------------------------------------ entrada do cliente

async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    texto = fmt.telegram_boas_vindas()
    if _aprovadores() is None:   # ajuda na configuração: o chat_id vai em TELEGRAM_APPROVERS_CHAT_ID
        texto += fmt.telegram_chat_id_dica(chat.id)
    await chat.send_message(texto, parse_mode=ParseMode.HTML)


async def _cmd_demo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    try:
        n = int((ctx.args or ["1"])[0])
    except ValueError:
        n = 0
    if not 1 <= n <= 5:
        await chat.send_message("Use /demo 1 a /demo 5.")
        return
    demo = None
    try:
        casos = json.loads((config.DATA_DIR / "demo_cases.json").read_text(encoding="utf-8"))
        demo = next((c for c in casos if int(c.get("n", 0)) == n), None)
    except (OSError, ValueError):
        pass
    if demo:
        body = {k: demo.get(k) for k in ("customer_id", "order_id", "conversation_id", "anexos") if demo.get(k)}
        body.update(canal="telegram", mensagem=demo.get("mensagem", ""), telegram_chat_id=str(chat.id), demo_caso=n)
        await chat.send_message(f"🎬 Demo {n}: {demo.get('titulo', '')}\n\nCliente: “{demo.get('mensagem', '')}”")
        await chat.send_message(fmt.telegram_confirmacao())
        st, resp = await comum.api("POST", "/api/goxys/casos", body)
    else:
        st, resp = await comum.api("POST", f"/api/goxys/simular/{n}")
    if st >= 400:
        await chat.send_message("Não consegui disparar a demo agora. Tente pelo painel.")
    else:
        await chat.send_message(f"Caso {resp.get('case_id', '?') if isinstance(resp, dict) else '?'} aberto. Acompanhe no painel.")


async def _baixar_foto(msg) -> dict | None:
    try:
        foto = msg.photo[-1]
        arq = await foto.get_file()
        UPLOADS.mkdir(parents=True, exist_ok=True)
        nome = f"tg_{msg.chat.id}_{foto.file_unique_id}.jpg"
        await arq.download_to_drive(UPLOADS / nome)
        return {"anexo_id": f"TG-{foto.file_unique_id}", "tipo": "foto_produto",
                "url": f"/assets-goxys/uploads/{nome}",
                "enviado_em": datetime.now().isoformat(timespec="seconds"),
                "descricao_visual": msg.caption or None}
    except Exception:
        log.exception("falha ao baixar foto do Telegram")
        return None


async def _msg_cliente(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None:
        return
    aprov = _aprovadores()
    if aprov is not None and chat.id == aprov:
        await _resposta_aprovador(update)
        return
    caso = comum.caso_com_pessoa_do_chat(chat.id)
    if caso:   # o caso deste chat está com uma pessoa: a mensagem entra na mesma conversa, sem abrir caso novo
        anexos = []
        if msg.photo:
            a = await _baixar_foto(msg)
            if a:
                anexos.append(a)
        st, _ = await comum.api("POST", f"/api/goxys/casos/{caso}/cliente",
                                {"texto": msg.text or msg.caption or "", "anexos": anexos})
        if st < 400:
            return
    buf = _buffers.get(chat.id)
    if buf is None:
        buf = _buffers[chat.id] = {"textos": [], "anexos": [], "task": None, "msg_id": msg.message_id}
        await msg.reply_text(fmt.telegram_confirmacao())            # confirmação imediata (SPEC §14)
    texto = msg.text or msg.caption
    if texto:
        buf["textos"].append(texto)
    if msg.photo:
        anexo = await _baixar_foto(msg)
        if anexo:
            buf["anexos"].append(anexo)
    if buf["task"]:
        buf["task"].cancel()
    buf["task"] = asyncio.create_task(_fechar_buffer(chat.id))


async def _fechar_buffer(chat_id: int) -> None:
    try:
        await asyncio.sleep(JANELA_S)
    except asyncio.CancelledError:
        return
    buf = _buffers.pop(chat_id, None)
    if not buf:
        return
    texto = "\n".join(buf["textos"]).strip() or "[foto anexada]"
    body: dict = {"canal": "telegram", "mensagem": texto, "telegram_chat_id": str(chat_id),
                  "conversation_id": f"TG-{chat_id}-{buf['msg_id']}", "confirmacao": fmt.telegram_confirmacao()}
    if buf["anexos"]:
        body["anexos"] = buf["anexos"]
    pedido = fmt.extrair_pedido(texto)
    if pedido:
        body["order_id"] = pedido
        try:
            st, p = await comum.api("GET", f"/api/erp/pedidos/{pedido}")
            if st == 200 and isinstance(p, dict) and p.get("customer_id"):
                body["customer_id"] = p["customer_id"]
        except Exception:
            pass
    try:
        st, resp = await comum.api("POST", "/api/goxys/casos", body)
        if st >= 400:
            raise RuntimeError(resp)
        log.info("caso %s aberto pelo Telegram (chat %s)", resp.get("case_id"), chat_id)
    except Exception:
        log.exception("falha ao abrir caso pelo Telegram")
        if _app:
            await _app.bot.send_message(chat_id, "Tive um problema para registrar seu pedido. Já avisei o time humano.")


# ------------------------------------------------------------------ aprovações

async def _callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parsed = fmt.parse_callback(q.data)
    if not parsed:
        await q.answer()
        return
    decisao, aid = parsed
    if decisao == "ajustar":
        await q.answer("Mande o novo valor respondendo à próxima mensagem.")
        pedido = await comum.pedido_aprovacao(aid)
        m = await q.message.reply_text(fmt.telegram_ajuste_prompt(pedido), parse_mode=ParseMode.HTML,
                                       reply_markup=ForceReply(selective=True, input_field_placeholder="350 nota..."))
        _prompts_ajuste[m.message_id] = aid
        return
    st, body = await comum.decidir(aid, decisao, _nome(q.from_user), "telegram")
    if st == 409:
        await q.answer("Essa aprovação já foi decidida.", show_alert=True)
    elif st >= 400:
        await q.answer(f"Não consegui registrar ({st}).", show_alert=True)
    else:
        await q.answer("Decisão registrada. O Goxy retoma o caso.")
    _postadas.setdefault(aid, (q.message.chat.id, q.message.message_id))


async def _resposta_aprovador(update: Update) -> None:
    msg = update.effective_message
    rep = msg.reply_to_message
    if not rep or rep.message_id not in _prompts_ajuste or not msg.text:
        return
    aid = _prompts_ajuste.pop(rep.message_id)
    ajuste = fmt.parse_ajuste(msg.text)
    st, _ = await comum.decidir(aid, "ajustar", _nome(msg.from_user), "telegram", ajuste or {"nota": msg.text})
    if st == 409:
        await msg.reply_text("Essa aprovação já tinha sido decidida.")
    elif st >= 400:
        await msg.reply_text(f"Não consegui registrar o ajuste ({st}).")
    else:
        await msg.reply_text("Ajuste registrado. ✏️")


async def _on_evento(evt: dict) -> None:
    if _app is None:
        return
    t = evt.get("type")
    d = evt.get("data") or {}
    bot = _app.bot
    if t == "message.sent":
        chat = d.get("telegram_chat_id") or (comum.telegram_chat_do_caso(evt.get("case_id"))
                                             if d.get("canal") == "telegram" else None)
        if chat and d.get("texto"):
            await bot.send_message(int(chat), d["texto"])
    elif t == "approval.requested":
        info = comum.lembrar_aprovacao(evt)
        chat = _aprovadores()
        aid = str(d.get("aprovacao_id") or "")
        if chat is None or not aid:
            return
        m = await bot.send_message(chat, fmt.telegram_aprovacao_texto(d, evt.get("case_id")),
                                   parse_mode=ParseMode.HTML, reply_markup=_teclado(aid))
        _postadas[aid] = (m.chat.id, m.message_id)
        info["telegram"] = _postadas[aid]
    elif t in ("approval.decided", "approval.escalated"):
        aid = str(d.get("aprovacao_id") or "")
        if aid not in _postadas:
            return
        chat, mid = _postadas[aid]
        info = comum.APROVACOES.get(aid, {})
        pedido, cid = info.get("pedido") or {"aprovacao_id": aid}, info.get("case_id") or evt.get("case_id")
        if t == "approval.decided":
            await bot.edit_message_text(fmt.telegram_decisao_texto(pedido, d, cid), chat_id=chat, message_id=mid,
                                        parse_mode=ParseMode.HTML, reply_markup=None)
        else:
            await bot.edit_message_text(fmt.telegram_escalado_texto(pedido, cid), chat_id=chat, message_id=mid,
                                        parse_mode=ParseMode.HTML, reply_markup=_teclado(aid))


# ------------------------------------------------------------------ ciclo de vida

_ultimo_aviso = {"t": 0.0}


def _erro_polling(err: TelegramError) -> None:
    """Erros do long polling (ex.: 409 Conflict quando outra instância usa o mesmo token): só avisa."""
    import time
    if time.monotonic() - _ultimo_aviso["t"] < 60:
        return
    _ultimo_aviso["t"] = time.monotonic()
    if isinstance(err, Conflict):
        log.warning("Telegram 409 Conflict: outra instância está fazendo polling com este token; "
                    "este processo segue sem receber mensagens (desligue a outra ou use TELEGRAM_BOT_TOKEN= vazio).")
    else:
        log.warning("Telegram polling: %s", err)


async def _erro_handler(update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.warning("Telegram: erro tratando update: %s", ctx.error)

async def start() -> None:
    global _app
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    _app = app          # cedo: se a partida falhar no meio, stop() ainda limpa
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("demo", _cmd_demo))
    app.add_handler(CallbackQueryHandler(_callback, pattern=r"^gx\|"))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO, _msg_cliente))
    await app.initialize()
    await app.start()
    app.add_error_handler(_erro_handler)
    await app.updater.start_polling(drop_pending_updates=True, error_callback=_erro_polling)
    try:
        await app.bot.set_my_commands([BotCommand("start", "O que o Goxy faz"),
                                       BotCommand("demo", "Dispara o caso de demo n (1 a 5)")])
    except Exception:
        pass
    global _inscrito
    if not _inscrito:
        bus.subscribe_listener(_on_evento)
        _inscrito = True
    me = await app.bot.get_me()
    log.info("canal Telegram ligado: @%s (aprovadores: %s)", me.username,
             config.TELEGRAM_APPROVERS_CHAT_ID or "não configurado")


async def stop() -> None:
    global _app
    app, _app = _app, None
    if app is None:
        return
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("erro ao desligar o Telegram")
