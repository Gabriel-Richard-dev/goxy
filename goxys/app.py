"""Processo único do Goxy.

    cd goxys && .venv/bin/uvicorn app:app --port 8000

Monta: sistemas simulados (/api/erp, /api/chamados, ... , /portal), a API do orquestrador
(/api/goxys/*, SSE em /api/goxys/stream), os canais (Telegram/Slack, se configurados),
os assets do mascote (/assets) e o painel React compilado (/).
"""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("goxys")


def _optional(module: str):
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        log.warning("módulo %s ainda não disponível (%s)", module, e)
        return None


mock_api = _optional("mock_api.routes")      # expõe: routers: list[APIRouter]; async startup()
goxys_api = _optional("api")                 # expõe: router: APIRouter; async startup()
channels = _optional("channels")             # expõe: async start(); async stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    for mod in (mock_api, goxys_api):
        if mod and hasattr(mod, "startup"):
            await mod.startup()
    if channels:
        await channels.start()
    log.info("Goxy no ar em %s | IA %s", config.PUBLIC_URL, "ligada" if config.llm_enabled() else "DESLIGADA (regras)")
    yield
    if channels:
        await channels.stop()
    for mod in (goxys_api, mock_api):
        if mod and hasattr(mod, "shutdown"):
            await mod.shutdown()


app = FastAPI(title="Goxy", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

if mock_api:
    for r in mock_api.routers:
        app.include_router(r)
if goxys_api:
    app.include_router(goxys_api.router)

config.ASSETS_DIR.mkdir(exist_ok=True)
app.mount("/assets-goxys", StaticFiles(directory=config.ASSETS_DIR), name="mascote")

if (config.WEB_DIST / "index.html").exists():
    app.mount("/app", StaticFiles(directory=config.WEB_DIST, html=True), name="web")

    @app.get("/", include_in_schema=False)
    async def root():
        # Landing na raiz; o mesmo bundle decide pela URL (/ = landing, /app = sistema)
        return FileResponse(config.WEB_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {"goxys": "ok", "painel": "rode `npm run dev` em goxys/web ou `npm run build`", "docs": "/docs"}
