"""Estado único do caso (ideia 7 da SPEC): todos os agentes leem e escrevem no mesmo objeto.

O CaseStore guarda os casos em memória e persiste um snapshot JSON em SQLite (tabela goxys_cases)
para auditoria. Toda mutação publica `case.updated` no barramento.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .bus import bus
from .config import DB_PATH

CaseStatus = Literal["novo", "em_andamento", "aguardando_aprovacao", "aguardando_cliente", "resolvido",
                     "rejeitado", "humano", "erro"]
StepStatus = Literal["pendente", "rodando", "ok", "alerta", "falhou", "pulado"]
MascotState = Literal["parado", "investigando", "fraude", "esperando", "resolvido", "humano"]

AGENTES = ["maestro", "porteiro", "rastreador", "detetive", "executor", "voz", "mensageiro", "analista"]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Anexo(BaseModel):
    anexo_id: Optional[str] = None
    tipo: Optional[str] = None
    hash_perceptual: Optional[str] = None
    exif_data_captura: Optional[str] = None
    exif_modelo_camera: Optional[str] = None
    exif_software: Optional[str] = None
    descricao_visual: Optional[str] = None
    url: Optional[str] = None            # imagem real, quando houver (ex.: foto mandada no Telegram)
    enviado_em: Optional[str] = None


class TimelineStep(BaseModel):
    agent: str
    status: StepStatus = "pendente"
    titulo: str = ""                      # frase curta: "classificou como avaria"
    detalhe: str = ""
    evidencias: list[dict] = Field(default_factory=list)
    inicio: Optional[str] = None
    fim: Optional[str] = None
    duracao_ms: Optional[int] = None


class ToolCall(BaseModel):
    acao_id: str
    agent: str
    ferramenta: str
    tipo: Literal["leitura", "escrita", "escrita_critica", "humano", "llm"]
    parametros: dict = Field(default_factory=dict)
    resultado: Any = None
    status_http: Optional[int] = None
    nivel_autonomia: Optional[str] = None
    ts: str = Field(default_factory=now)
    duracao_ms: Optional[int] = None


class Case(BaseModel):
    id: str
    canal: str = "whatsapp"               # whatsapp | email | reclame_aqui | telegram | painel
    customer_id: Optional[str] = None
    order_id: Optional[str] = None
    conversation_id: Optional[str] = None
    demo_caso: Optional[int] = None       # 1..5 quando disparado pelo simulador
    cliente_nome: Optional[str] = None
    mensagem: str = ""
    mensagens: list[dict] = Field(default_factory=list)   # [{autor, texto, ts}]
    anexos: list[Anexo] = Field(default_factory=list)
    telegram_chat_id: Optional[str] = None
    status: CaseStatus = "novo"
    mascot_state: MascotState = "parado"
    criado_em: str = Field(default_factory=now)
    atualizado_em: str = Field(default_factory=now)
    encerrado_em: Optional[str] = None

    plano: list[dict] = Field(default_factory=list)        # grafo do Maestro: [{agent, depende_de:[...]}]
    triagem: dict = Field(default_factory=dict)            # intencao, urgencia, sentimento, idioma, risco_juridico, imprensa, confianca
    dossie: dict = Field(default_factory=dict)             # pedido, cliente, rastreio, historico, fonte_rastreio, inconsistencias
    veracidade: dict = Field(default_factory=dict)         # score, faixa, evidencias, comparacao
    decisao: dict = Field(default_factory=dict)            # acao, valor, nivel, motivo, politica_versao, sinais, revisor
    aprovacao: dict = Field(default_factory=dict)          # aprovacao_id, status, decisao, aprovador, canal, tempo_resposta_s
    resposta: dict = Field(default_factory=dict)           # texto, canal, message_id
    acoes: list[ToolCall] = Field(default_factory=list)
    timeline: list[TimelineStep] = Field(default_factory=list)
    llm_uso: dict = Field(default_factory=lambda: {"chamadas": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
    # humano no caso (passar/devolver): {colaborador_id, nome, cargo, desde, nota}
    responsavel: Optional[dict] = None
    resumo_passagem: str = ""                              # 3 linhas: o que o cliente quer / o que achamos / o que falta
    sugestao_responsavel: Optional[dict] = None            # {colaborador_id, nome, cargo, motivo} (níveis C e D)
    grafo: dict = Field(default_factory=dict)              # LangGraph: {thread_id, execucoes}

    def step(self, agent: str) -> TimelineStep:
        for s in self.timeline:
            if s.agent == agent:
                return s
        s = TimelineStep(agent=agent)
        self.timeline.append(s)
        return s


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._seq = itertools.count(1)
        self._lock = asyncio.Lock()
        self._acao_seq = itertools.count(1)
        self._db_ready = False

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(DB_PATH)
        if not self._db_ready:
            con.execute("CREATE TABLE IF NOT EXISTS goxys_cases (id TEXT PRIMARY KEY, snapshot TEXT, atualizado_em TEXT)")
            self._db_ready = True
        return con

    def new_id(self) -> str:
        return f"GX{datetime.now():%H%M}{next(self._seq):03d}"

    def new_acao_id(self) -> str:
        return f"GXA{next(self._acao_seq):06d}"

    async def create(self, **fields: Any) -> Case:
        case = Case(id=self.new_id(), **fields)
        self._cases[case.id] = case
        await self.save(case, event="case.created")
        return case

    def carregar_do_banco(self) -> int:
        """Recarrega os snapshots de goxys_cases (reinício do servidor). O contador de ids continua depois do maior."""
        try:
            with self._db() as con:
                rows = con.execute("SELECT snapshot FROM goxys_cases").fetchall()
        except sqlite3.Error:
            return 0
        maior = 0
        for (snap,) in rows:
            try:
                c = Case(**json.loads(snap))
            except Exception:
                continue
            self._cases[c.id] = c
            try:
                maior = max(maior, int(c.id[-3:]))
            except ValueError:
                pass
            for a in c.acoes:
                try:
                    self._acao_seq_min = max(getattr(self, "_acao_seq_min", 0), int(a.acao_id[3:]))
                except ValueError:
                    pass
        self._seq = itertools.count(maior + 1)
        self._acao_seq = itertools.count(getattr(self, "_acao_seq_min", 0) + 1)
        return len(rows)

    def limpar(self) -> list[str]:
        """Apaga todos os casos da memória e do snapshot (limpar demo)."""
        ids = list(self._cases)
        self._cases.clear()
        try:
            with self._db() as con:
                con.execute("DELETE FROM goxys_cases")
        except sqlite3.Error:
            pass
        return ids

    def get(self, case_id: str) -> Case | None:
        return self._cases.get(case_id)

    def all(self) -> list[Case]:
        return sorted(self._cases.values(), key=lambda c: c.criado_em, reverse=True)

    async def save(self, case: Case, event: str = "case.updated") -> None:
        case.atualizado_em = now()
        snap = case.model_dump()
        try:
            with self._db() as con:
                con.execute("INSERT OR REPLACE INTO goxys_cases VALUES (?,?,?)",
                            (case.id, json.dumps(snap, ensure_ascii=False, default=str), case.atualizado_em))
        except sqlite3.Error:
            pass
        await bus.publish(event, {"case": snap}, case_id=case.id)

    async def set_status(self, case: Case, status: CaseStatus, mascot: MascotState | None = None) -> None:
        case.status = status
        if mascot:
            case.mascot_state = mascot
        if status in ("resolvido", "rejeitado", "humano"):
            case.encerrado_em = now()
        await self.save(case)
        if mascot:
            await bus.publish("mascot.state", {"state": mascot}, case_id=case.id)


store = CaseStore()
