"""Configuração central do Goxy. Tudo vem de variáveis de ambiente (.env na raiz de goxys/)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent          # goxys/
REPO = ROOT.parent                                     # gohacks/
load_dotenv(ROOT / ".env")

DATA_RAW = REPO / "data_raw" / "GoHacks_Talent_2026_bases_csv"
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("GOXYS_DB", DATA_DIR / "goxys.db"))
ASSETS_DIR = REPO / "assets"
WEB_DIST = ROOT / "web" / "dist"
POLICY_PATH = ROOT / "policy" / "autonomia.yaml"

# Servidor único: sistemas simulados + orquestrador + painel
HOST = os.getenv("GOXYS_HOST", "0.0.0.0")
PORT = int(os.getenv("GOXYS_PORT", "8000"))
# Os agentes falam com os sistemas simulados por HTTP, como falariam com sistemas reais.
SYSTEMS_BASE_URL = os.getenv("GOXYS_SYSTEMS_URL", f"http://127.0.0.1:{PORT}")
PUBLIC_URL = os.getenv("GOXYS_PUBLIC_URL", f"http://localhost:{PORT}")

# IA: AI Proxy do evento (formato OpenAI). Sem chave, os agentes caem para regras determinísticas.
AI_PROXY_BASE_URL = os.getenv("AI_PROXY_BASE_URL", "https://ai-proxy.gogroupbr.com/v1")
AI_PROXY_API_KEY = os.getenv("AI_PROXY_API_KEY", "")
LLM_MODEL = os.getenv("GOXYS_LLM_MODEL", "latest")          # nunca deepseek-*
LLM_FAST_MODEL = os.getenv("GOXYS_LLM_FAST_MODEL", "glm-5.3-flash")
LLM_TIMEOUT_S = float(os.getenv("GOXYS_LLM_TIMEOUT", "40"))

# Canais (opcionais; sem token o canal fica desligado e a interface é o plano B)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_APPROVERS_CHAT_ID = os.getenv("TELEGRAM_APPROVERS_CHAT_ID", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")          # xapp-... (Socket Mode)
SLACK_APPROVALS_CHANNEL = os.getenv("SLACK_APPROVALS_CHANNEL", "")

# Ritmo da demo: atraso artificial entre passos para a linha do tempo "acender" visivelmente
DEMO_STEP_DELAY_S = float(os.getenv("GOXYS_STEP_DELAY", "0.8"))
APPROVAL_SLA_S = int(os.getenv("GOXYS_APPROVAL_SLA_S", str(30 * 60)))

# "Agora" do ambiente de dados
NOW_ISO = os.getenv("GOXYS_NOW", "2026-11-02 08:00:00")


def llm_enabled() -> bool:
    return bool(AI_PROXY_API_KEY)
