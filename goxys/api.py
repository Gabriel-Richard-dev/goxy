"""API do orquestrador Goxy (CONTRACTS §5), prefixo /api/goxys.

Expõe `router` e `async def startup()` (registra o Maestro no barramento para retomar aprovações).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agents import equipe, grafo, maestro, passagem
from agents.porteiro import ROTULO
from core import config, llm
from core.bus import bus
from core.state import Anexo, Case, TimelineStep, now, store

log = logging.getLogger("goxys.api")
router = APIRouter(prefix="/api/goxys", tags=["Goxy"])
_tasks: set[asyncio.Task] = set()


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def startup() -> None:
    maestro.registrar_listener()
    await grafo.startup()       # LangGraph + checkpoint SQLite; recarrega casos e retoma o que estava pendente
    log.info("Goxy: maestro (LangGraph) escutando approval.decided")


async def shutdown() -> None:
    await grafo.shutdown()


# ------------------------------------------------------------------ casos

class NovoCaso(BaseModel):
    canal: str = "painel"
    mensagem: str
    customer_id: Optional[str] = None
    order_id: Optional[str] = None
    conversation_id: Optional[str] = None
    anexos: list[dict] = []
    telegram_chat_id: Optional[str] = None
    processar: bool = True
    confirmacao: Optional[str] = None      # texto da confirmação que o canal já mandou (ex.: bot do Telegram)


async def _criar(canal: str, mensagem: str, *, processar: bool = True, **campos: Any) -> Case:
    anexos = [Anexo(**{k: v for k, v in a.items() if k in Anexo.model_fields}) for a in campos.pop("anexos", None) or []]
    confirmacao = campos.pop("confirmacao", None)
    mensagens = [{"autor": "cliente", "texto": mensagem, "ts": now(), "canal": canal}]
    if confirmacao:
        mensagens.append({"autor": "goxys", "nome": "Goxy", "tipo": "confirmacao", "texto": confirmacao,
                          "ts": now(), "canal": canal})
    case = await store.create(canal=canal, mensagem=mensagem, anexos=anexos, mensagens=mensagens,
                              **{k: v for k, v in campos.items() if v is not None})
    if processar:
        _bg(maestro.handle(case.id))
    return case


@router.post("/casos")
async def criar_caso(body: NovoCaso, request: Request):
    # X-Goxy-Politica: caso de validação de uma sugestão de política (fica fora da fila e das métricas)
    sug = request.headers.get("X-Goxy-Politica")
    c = await _criar(body.canal, body.mensagem, processar=body.processar, customer_id=body.customer_id,
                     grafo={"politica_override": sug, "validacao": True} if sug else None,
                     order_id=body.order_id, conversation_id=body.conversation_id, anexos=body.anexos,
                     telegram_chat_id=str(body.telegram_chat_id) if body.telegram_chat_id else None,
                     confirmacao=body.confirmacao)
    return {"case_id": c.id}


def _demo_cases() -> list[dict]:
    p = config.DATA_DIR / "demo_cases.json"
    if not p.exists():
        raise HTTPException(404, "data/demo_cases.json não encontrado")
    return json.loads(p.read_text(encoding="utf-8"))


def _reset_demo(order_id: str | None) -> None:
    """Reensaio da demo: apaga as escritas simuladas anteriores deste pedido (reembolso, troca, chamado na
    transportadora), para o caso 4 não bater em `valor_acima_do_pedido` na segunda vez. O log (sim_eventos) fica."""
    if not order_id:
        return
    try:
        with _db() as con:
            for t in ("sim_reembolsos", "sim_trocas", "sim_chamados_transportadora"):
                con.execute(f"DELETE FROM {t} WHERE order_id=?", (order_id,))
    except sqlite3.Error as e:
        log.warning("reset da demo falhou: %s", e)


async def _simular(n: int) -> Case:
    d = next((x for x in _demo_cases() if int(x["n"]) == n), None)
    if d is None:
        raise HTTPException(404, f"caso de demo {n} não existe")
    await asyncio.to_thread(_reset_demo, d.get("order_id"))
    return await _criar(d.get("canal", "whatsapp"), d["mensagem"], customer_id=d.get("customer_id"),
                        order_id=d.get("order_id"), conversation_id=d.get("conversation_id"),
                        anexos=d.get("anexos") or [], demo_caso=n)


@router.post("/simular/todos")
async def simular_todos():
    ids = []
    for i, d in enumerate(_demo_cases()):
        if i:
            await asyncio.sleep(1.5)
        ids.append((await _simular(int(d["n"]))).id)
    return {"case_ids": ids}


@router.post("/simular/{n}")
async def simular(n: int):
    return {"case_id": (await _simular(n)).id}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


@router.post("/fila/importar")
async def importar_fila(limite: int = Query(10, ge=1, le=200)):
    ja = {c.conversation_id for c in store.all() if c.conversation_id}

    def ler():
        with _db() as con:
            fila = [dict(r) for r in con.execute("SELECT * FROM fila_aberta ORDER BY data_abertura")]
            out = []
            for f in fila:
                if f["conversation_id"] in ja:
                    continue
                msgs = [dict(r) for r in con.execute(
                    "SELECT autor, texto, timestamp FROM historico_mensagens WHERE conversation_id=? ORDER BY timestamp",
                    (f["conversation_id"],))]
                cli = [m for m in msgs if m["autor"] == "cliente" and (m["texto"] or "").strip()]
                if not cli:
                    continue
                anexos = [dict(r) for r in con.execute("SELECT * FROM anexos_conversas WHERE conversation_id=?",
                                                       (f["conversation_id"],))]
                out.append((f, msgs, cli, anexos))
                if len(out) >= limite:
                    break
            return out

    try:
        itens = await asyncio.to_thread(ler)
    except sqlite3.Error as e:
        raise HTTPException(503, f"banco indisponível: {e}")
    ids = []
    for f, msgs, cli, anexos in itens:
        texto = "\n".join(m["texto"] for m in cli)
        c = await _criar(f.get("canal") or "whatsapp", texto, processar=False, customer_id=f.get("customer_id"),
                         order_id=f.get("order_id"), conversation_id=f["conversation_id"], anexos=anexos)
        c.mensagens = [{"autor": m["autor"], "texto": m["texto"], "ts": m["timestamp"], "canal": c.canal} for m in msgs]
        await store.save(c)
        ids.append(c.id)
    return {"importados": len(ids), "case_ids": ids}


@router.post("/casos/{case_id}/processar")
async def processar(case_id: str):
    c = store.get(case_id)
    if not c:
        raise HTTPException(404, "caso não encontrado")
    if c.status not in ("novo", "erro"):
        raise HTTPException(409, f"caso já está {c.status}")
    c.timeline = []
    _bg(maestro.handle(case_id))
    return {"case_id": case_id, "status": "em_andamento"}


# ------------------------------------------------------------------ humano no caso

def _erro(e: passagem.ErroPassagem):
    raise HTTPException(e.status, str(e))


@router.get("/equipe")
async def listar_equipe():
    return await asyncio.to_thread(equipe.listar)


class Passar(BaseModel):
    para: str
    nota: Optional[str] = None


@router.post("/casos/{case_id}/passar")
async def passar_caso(case_id: str, body: Passar):
    try:
        c = await passagem.passar(case_id, body.para, body.nota)
    except passagem.ErroPassagem as e:
        _erro(e)
    return {"case_id": c.id, "status": c.status, "responsavel": c.responsavel, "resumo_passagem": c.resumo_passagem}


class Devolver(BaseModel):
    nota: Optional[str] = None
    instrucao: str = "seguir"


@router.post("/casos/{case_id}/devolver")
async def devolver_caso(case_id: str, body: Devolver):
    try:
        c = await passagem.devolver(case_id, body.nota, body.instrucao)
    except passagem.ErroPassagem as e:
        _erro(e)
    return {"case_id": c.id, "status": c.status, "instrucao": body.instrucao}


class MensagemHumana(BaseModel):
    texto: str
    autor: str


@router.post("/casos/{case_id}/mensagens")
async def mensagem_humana(case_id: str, body: MensagemHumana):
    try:
        return await passagem.enviar_humano(case_id, body.texto, body.autor)
    except passagem.ErroPassagem as e:
        _erro(e)


class MensagemCliente(BaseModel):
    texto: str = ""
    anexos: list[dict] = []


@router.post("/casos/{case_id}/cliente")
async def mensagem_do_cliente(case_id: str, body: MensagemCliente):
    """Cliente escreveu de novo num caso que está com uma pessoa (ex.: Telegram): entra na mesma conversa."""
    try:
        return await passagem.mensagem_cliente(case_id, body.texto, body.anexos)
    except passagem.ErroPassagem as e:
        _erro(e)


@router.post("/casos/{case_id}/sugerir-resposta")
async def sugerir_resposta(case_id: str):
    try:
        return {"texto": await passagem.sugerir_resposta(case_id)}
    except passagem.ErroPassagem as e:
        _erro(e)


@router.get("/grafo")
async def ver_grafo():
    return grafo.descrever()


@router.post("/demo/limpar")
async def limpar_demo():
    """Apaga os casos (memória + snapshot + checkpoints) e as escritas simuladas dos pedidos de demo."""
    grafo.cancelar_tudo()
    ids = store.limpar()
    await grafo.apagar_threads(ids)
    demos = _demo_cases() if (config.DATA_DIR / "demo_cases.json").exists() else []
    pedidos = [d.get("order_id") for d in demos if d.get("order_id")]
    convs = [d.get("conversation_id") for d in demos if d.get("conversation_id")]

    def limpar():
        apagado = {}
        with _db() as con:
            def run(sql, params):
                try:
                    return con.execute(sql, params).rowcount
                except sqlite3.Error:
                    return 0
            for t in ("sim_reembolsos", "sim_trocas", "sim_chamados_transportadora", "sim_chamados"):
                apagado[t] = sum(run(f"DELETE FROM {t} WHERE order_id=?", (o,)) for o in pedidos)
            apagado["sim_mensagens"] = sum(run("DELETE FROM sim_mensagens WHERE conversation_id=? OR case_id=?", (c, c))
                                           for c in convs + ids)
            apagado["sim_aprovacoes"] = run("DELETE FROM sim_aprovacoes WHERE status='pendente'", ())
            apagado["sim_eventos"] = sum(run("DELETE FROM sim_eventos WHERE case_id=?", (i,)) for i in ids)
        return apagado

    apagado = await asyncio.to_thread(limpar)
    bus._history.clear()
    await bus.publish("demo.reset", {"casos_apagados": len(ids), "apagado": apagado})
    return {"casos_apagados": len(ids), "apagado": apagado}


@router.post("/casos/{case_id}/desfazer")
async def desfazer(case_id: str, body: dict = Body(default={})):
    """Nível B: cancela/estorna de verdade as escritas do caso, até 15 min depois da ação (relógio real)."""
    from agents import executor
    c = store.get(case_id)
    if not c:
        raise HTTPException(404, "caso não encontrado")
    try:
        d = await executor.desfazer(c, body.get("por") or "supervisor", body.get("canal") or "painel")
    except executor.PrazoExpirado as e:
        return JSONResponse({"erro": "prazo_expirado", "mensagem": f"Não dá mais para desfazer: {e}."}, status_code=409)
    except LookupError as e:
        return JSONResponse({"erro": "nada_a_desfazer", "mensagem": str(e)}, status_code=409)
    try:
        with open(config.DATA_DIR / "aprendizado.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), "case_id": case_id, "nivel": "B", "decisao_humana": "desfazer",
                                "acao": c.decisao.get("acao"), "politica_versao": c.decisao.get("politica_versao")},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {"case_id": case_id, "desfeito": d, "status": c.status}


class Sla(BaseModel):
    segundos: int


@router.post("/demo/sla")
async def mudar_sla(body: Sla):
    """Muda o prazo das aprovações em tempo de execução (demo). Rearma as pendentes com o prazo novo."""
    if body.segundos < 1:
        raise HTTPException(422, "segundos deve ser ≥ 1")
    config.APPROVAL_SLA_S = int(body.segundos)
    n = grafo.rearmar_sla()
    return {"sla_s": config.APPROVAL_SLA_S, "aprovacoes_rearmadas": n}


# ------------------------------------------------------------------ aprendizado

@router.get("/aprendizado")
async def aprendizado_resumo():
    from agents import aprendizado
    return await asyncio.to_thread(aprendizado.resumo)


@router.post("/aprendizado/sugestoes/{sid}/validar")
async def aprendizado_validar(sid: str):
    from agents import aprendizado
    if sid not in aprendizado.SUGESTOES:
        await asyncio.to_thread(aprendizado.gerar_sugestoes)
    try:
        job = aprendizado.validar(sid)
    except KeyError:
        raise HTTPException(404, "sugestão não encontrada")
    return job


@router.get("/aprendizado/jobs/{job_id}")
async def aprendizado_job(job_id: str):
    from agents import aprendizado
    job = aprendizado.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    return {**job, "sugestao": aprendizado.SUGESTOES.get(job["sugestao_id"])}


@router.post("/aprendizado/sugestoes/{sid}/aplicar")
async def aprendizado_aplicar(sid: str, body: dict = Body(default={})):
    from agents import aprendizado
    try:
        return await aprendizado.aplicar(sid, body.get("por") or "supervisor")
    except KeyError:
        raise HTTPException(404, "sugestão não encontrada")
    except ValueError as e:
        return JSONResponse({"erro": "nao_validada", "mensagem": str(e)}, status_code=409)


def _titulo(c: Case) -> str:
    if c.demo_caso:
        try:
            d = next((x for x in _demo_cases() if int(x["n"]) == c.demo_caso), None)
            if d:
                return d["titulo"]
        except HTTPException:
            pass
    i = (c.triagem or {}).get("intencao")
    if i:
        return ROTULO.get(i, i).capitalize()
    return (c.mensagem or "")[:60]


@router.get("/casos")
async def listar():
    return [{"id": c.id, "canal": c.canal, "titulo": _titulo(c), "status": c.status,
             "nivel": (c.decisao or {}).get("nivel"), "cliente_nome": c.cliente_nome, "criado_em": c.criado_em,
             "mascot_state": c.mascot_state, "demo_caso": c.demo_caso} for c in store.all()
            if not (c.grafo or {}).get("validacao")]


@router.get("/casos/{case_id}")
async def detalhe(case_id: str):
    c = store.get(case_id)
    if not c:
        raise HTTPException(404, "caso não encontrado")
    return c.model_dump()


# ------------------------------------------------------------------ SSE

@router.get("/stream")
async def stream(since: int = 0):
    async def gen():
        it = bus.stream(since).__aiter__()
        yield ": conectado\n\n"
        pending = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(it.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=15)
                if not done:
                    yield ": ping\n\n"
                    continue
                evt = pending.result()
                pending = None
                yield f"id: {evt['id']}\ndata: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n"
        finally:
            if pending is not None:
                pending.cancel()
            await it.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ métricas

CUSTO_CANAL = {"whatsapp": 4.8, "email": 7.2, "reclame_aqui": 42.0}


@lru_cache(maxsize=1)
def _baseline() -> dict:
    out = {"tempo_medio_humano_min": None, "primeira_resposta_humana_min": None, "custo_medio_conversa": None,
           "fonte": "tempo_por_conversa + parametros.md"}
    try:
        with _db() as con:
            r = con.execute("SELECT AVG(minutos_trabalhados) t, AVG(minutos_ate_primeira_resposta) p, "
                            "AVG(horas_ate_ultima_mensagem) h FROM tempo_por_conversa").fetchone()
            out["tempo_medio_humano_min"] = round(r["t"] or 0, 1)
            out["primeira_resposta_humana_min"] = round(r["p"] or 0, 1)
            out["horas_ate_resolver"] = round(r["h"] or 0, 1)
            mix = {row["canal"]: row["n"] for row in con.execute(
                "SELECT canal, COUNT(*) n FROM historico_conversas GROUP BY canal")}
            tot = sum(mix.values()) or 1
            out["custo_medio_conversa"] = round(sum(CUSTO_CANAL.get(k, 4.8) * n for k, n in mix.items()) / tot, 2)
            out["mix_canais"] = {k: round(n / tot, 3) for k, n in mix.items()}
    except sqlite3.Error as e:
        out["erro"] = str(e)
        out["tempo_medio_humano_min"] = 12.0
        out["custo_medio_conversa"] = 8.5
    return out


def _segundos(a: str | None, b: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except (TypeError, ValueError):
        return None


def _analista_mod():
    try:
        from agents import analista
        return analista
    except Exception:
        return None


@router.get("/metricas")
async def metricas():
    casos = [c for c in store.all() if c.status != "novo" and not (c.grafo or {}).get("validacao")]
    total = len(casos)
    por_nivel = {k: 0 for k in "ABCD"}
    por_status: dict[str, int] = {}
    fechados, aprov_t = [], []
    fraude_rs, fraudes, autonomos = 0.0, 0, 0
    for c in casos:
        n = (c.decisao or {}).get("nivel")
        if n in por_nivel:
            por_nivel[n] += 1
        por_status[c.status] = por_status.get(c.status, 0) + 1
        if c.encerrado_em:
            s = _segundos(c.criado_em, c.encerrado_em)
            if s is not None:
                fechados.append(s)
        if n in ("A", "B") and c.status == "resolvido":
            autonomos += 1
        if (c.decisao or {}).get("acao") == "negar" or ((c.veracidade or {}).get("fraude_objetiva") and c.status == "rejeitado"):
            fraudes += 1
            fraude_rs += float(((c.dossie or {}).get("pedido") or {}).get("valor") or (c.decisao or {}).get("valor") or 0)
        t = (c.aprovacao or {}).get("tempo_resposta_s")
        if t is not None:
            aprov_t.append(float(t))
    contatos = 0
    a = _analista_mod()
    if a and hasattr(a, "insights"):
        try:
            ins = await asyncio.to_thread(a.insights)
            contatos = int(ins.get("contatos_evitados") or ins.get("resumo", {}).get("contatos_evitaveis_total") or 0) \
                if isinstance(ins, dict) else 0
        except Exception:
            contatos = 0
    return {
        "total": total,
        "autonomos_pct": round(100 * autonomos / total, 1) if total else 0.0,
        "tempo_medio_s": round(sum(fechados) / len(fechados), 1) if fechados else None,
        "fraude_evitada_rs": round(fraude_rs, 2),
        "fraudes_barradas": fraudes,
        "aprovacoes_pendentes": sum(1 for c in casos if c.status == "aguardando_aprovacao"),
        "tempo_medio_aprovacao_s": round(sum(aprov_t) / len(aprov_t), 1) if aprov_t else None,
        "contatos_evitados": contatos,
        "por_nivel": por_nivel,
        "por_status": por_status,
        "llm": {"chamadas": llm.SESSION["chamadas"], "tokens": llm.SESSION["tokens_in"] + llm.SESSION["tokens_out"],
                "usd": round(llm.SESSION["usd"], 6)},
        "baseline": await asyncio.to_thread(_baseline),
    }


# ------------------------------------------------------------------ analista, placar, mascote, saúde

@router.get("/analista")
async def analista():
    a = _analista_mod()
    if a and hasattr(a, "insights"):
        try:
            return await asyncio.to_thread(a.insights)
        except Exception as e:
            log.exception("analista falhou")
            return {"clusters": [], "erro": str(e)}

    def minimo():
        with _db() as con:
            rows = con.execute("SELECT p.transportadora, p.uf, COUNT(*) n FROM historico_conversas h JOIN pedidos p "
                               "ON p.order_id=h.order_id WHERE h.tag_agente IN ('Atraso','Rastreio') "
                               "GROUP BY 1,2 ORDER BY n DESC LIMIT 5").fetchall()
        return {"clusters": [{"tipo": "transportadora_uf", "titulo": f"{r['transportadora']} × {r['uf']}",
                              "tamanho": r["n"]} for r in rows], "versao": "minima"}
    try:
        return await asyncio.to_thread(minimo)
    except sqlite3.Error as e:
        return {"clusters": [], "erro": str(e)}


@router.get("/placar")
async def placar():
    p = config.DATA_DIR / "placar.json"
    if not p.exists():
        return {"disponivel": False, "mensagem": "rode scripts/clientes_sinteticos.py para gerar o placar"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return {"disponivel": False, "erro": str(e)}


AGENTES_MASCOTE = ["maestro", "porteiro", "rastreador", "detetive", "executor", "voz", "mensageiro", "analista"]
ESTADOS_MASCOTE = ["investigando", "fraude", "esperando", "resolvido", "humano"]
EXT_VIDEO = (".webm", ".mp4")
EXT_IMG = (".webp", ".png", ".jpg", ".gif")


@router.get("/mascote")
async def mascote():
    base_dir: Path = config.ASSETS_DIR
    manifest = base_dir / "goxys" / "manifest.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            pass
    arquivos: list[Path] = []
    if base_dir.exists():
        arquivos = sorted(p for p in base_dir.rglob("*") if p.is_file() and not any(part.startswith("_") or part == "uploads" for part in p.relative_to(base_dir).parts) and p.suffix.lower() in EXT_VIDEO + EXT_IMG)

    def url(p: Path) -> str:
        return "/assets-goxys/" + p.relative_to(base_dir).as_posix()

    def achar(nome: str, exts: tuple[str, ...]) -> str | None:
        for ext in exts:
            for p in arquivos:
                if nome in p.stem.lower() and p.suffix.lower() == ext:
                    return url(p)
        return None

    out: dict[str, Any] = {"agentes": {}, "estados": {}}
    for a in AGENTES_MASCOTE:
        img, vid = achar(a, (".webp", ".png", ".jpg", ".gif")), achar(a, EXT_VIDEO)
        if img or vid:
            out["agentes"][a] = {"img": img, "video": vid}
    for e in ESTADOS_MASCOTE:
        vid, poster = achar(e, (".webm", ".mp4")), achar(e, (".png", ".webp", ".jpg"))
        if vid or poster:
            out["estados"][e] = {"video": vid, "poster": poster}
    if (base_dir / "goxy.png").exists():
        out["base"] = "/assets-goxys/goxy.png"
    return out


@router.get("/saude")
async def saude():
    db = False
    try:
        with _db() as con:
            con.execute("SELECT 1 FROM pedidos LIMIT 1").fetchone()
        db = True
    except sqlite3.Error:
        db = False
    return {"ia": llm.enabled(), "telegram": bool(config.TELEGRAM_BOT_TOKEN),
            "slack": bool(config.SLACK_BOT_TOKEN and config.SLACK_APP_TOKEN), "db": db,
            "politica_versao": _politica_versao()}


def _politica_versao() -> str | None:
    try:
        from agents.politica import versao
        return versao()
    except Exception:
        return None
