"""Canais do Goxy (WhatsApp e Telegram para o cliente, Telegram e Slack para aprovadores e o time).

Cada canal só liga se o token existir; sem token (ou se a conexão falhar) loga e segue: o painel é o plano B.
"""
from __future__ import annotations

import asyncio
import logging

from core import config

log = logging.getLogger("goxys.channels")

from . import whatsapp as _whatsapp

ATIVOS: dict[str, object] = {}
TIMEOUT_S = 20
# Rotas HTTP dos canais (webhooks); o app.py monta. O webhook responde 503 se o canal estiver desligado.
routers = [_whatsapp.router]


def status() -> dict:
    return {"telegram": "telegram" in ATIVOS, "slack": "slack" in ATIVOS, "reclame_aqui": "reclame_aqui" in ATIVOS,
            "whatsapp": "whatsapp" in ATIVOS}


async def _ligar(nome: str, tem_token: bool, importar) -> None:
    if not tem_token:
        log.info("canal %s desligado (sem token no .env)", nome)
        return
    try:
        mod = importar()
        await asyncio.wait_for(mod.start(), TIMEOUT_S)
        ATIVOS[nome] = mod
    except Exception as e:  # nunca derruba o app
        try:
            await asyncio.wait_for(mod.stop(), 5)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        log.warning("canal %s desligado: falhou ao conectar (%s: %s)", nome, type(e).__name__, e)


async def start() -> None:
    def _tg():
        from . import telegram
        return telegram

    def _sl():
        from . import slack
        return slack

    def _ra():
        from . import reclame_aqui
        return reclame_aqui

    await asyncio.gather(
        _ligar("reclame_aqui", True, _ra),  # sem token: fala com a API do Reclame Aqui (simulada)
        _ligar("telegram", bool(config.TELEGRAM_BOT_TOKEN), _tg),
        _ligar("slack", bool(config.SLACK_BOT_TOKEN and config.SLACK_APP_TOKEN), _sl),
        _ligar("whatsapp", _whatsapp.configurado(), lambda: _whatsapp),
    )


async def stop() -> None:
    for nome, mod in list(ATIVOS.items()):
        try:
            await asyncio.wait_for(mod.stop(), 10)
        except Exception as e:
            log.warning("erro ao desligar canal %s: %s", nome, e)
        ATIVOS.pop(nome, None)
