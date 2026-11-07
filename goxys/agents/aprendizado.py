"""Aprendizado com as decisões humanas (SPEC §14 ideia 5), sem deixar a IA se reescrever sozinha.

1. Concordância Goxy × pessoas: cada aprovação guarda `recomendacao`; ao decidir, `concordou`.
2. Histórico REAL da base: como os supervisores decidiram, por faixa de valor (`acoes_executadas_sistemas.aprovado_por`,
   `trocas_reenvios.aprovado_por`). Ressalva honesta: a base só registra ações executadas, recusas não aparecem.
3. Sugestões de limite nascem desses números. Uma sugestão só entra depois de passar em todos os clientes sintéticos
   (job em background, política proposta em memória só para os casos da validação) e de alguém aplicar.
4. Aplicar grava uma nova versão do YAML (a anterior vai para `policy/historico/`) e publica `policy.updated`.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import itertools
import json
import logging
import re
import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml

from core import config
from core.bus import bus
from core.state import now, store

from . import politica

log = logging.getLogger("goxys.aprendizado")

VALIDADORES = {"placar-sintetico", "validador-sintetico"}         # aprovações automáticas: fora da concordância
ACAO_REAL = {"reenvio": "reenviar_produto", "troca": "trocar_produto", "nova_producao": "reimprimir_personalizacao",
             "reembolso": "reembolsar"}
NOME = {"reenvio": "reenvios", "troca": "trocas", "nova_producao": "novas produções", "reembolso": "reembolsos"}
FAIXAS = [0, 100, 150, 200, 300, 500]
MIN_AMOSTRA = 10

SUGESTOES: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
_job_seq = itertools.count(1)
_tasks: set[asyncio.Task] = set()


def _arquivo() -> Path:
    return config.DATA_DIR / "sugestoes_politica.json"


def _salvar() -> None:
    try:
        _arquivo().write_text(json.dumps(SUGESTOES, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


def _carregar_registro() -> None:
    if SUGESTOES or not _arquivo().exists():
        return
    try:
        SUGESTOES.update(json.loads(_arquivo().read_text(encoding="utf-8")))
    except ValueError:
        pass


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _brl(v) -> str:
    return f"R$ {float(v):,.0f}".replace(",", ".")


# ------------------------------------------------------------------ dados

def historico_real() -> list[dict]:
    """Decisões reais dos supervisores por ação e faixa de valor."""
    out = []
    try:
        with _db() as con:
            for acao, real in ACAO_REAL.items():
                for lo, hi in zip(FAIXAS, FAIXAS[1:] + [None]):
                    sql = ("SELECT COUNT(*) n, SUM(aprovado_por IS NOT NULL AND aprovado_por<>'') aprov, "
                           "COUNT(DISTINCT aprovado_por) quem FROM acoes_executadas_sistemas WHERE acao=? AND valor_rs>?")
                    params: list = [real, lo]
                    if hi is not None:
                        sql += " AND valor_rs<=?"
                        params.append(hi)
                    r = con.execute(sql, params).fetchone()
                    if r["n"]:
                        out.append({"acao": acao, "faixa": [lo, hi], "executadas": r["n"], "com_aprovacao": r["aprov"] or 0,
                                    "supervisores": r["quem"], "fonte": "acoes_executadas_sistemas"})
            r = con.execute("SELECT COUNT(*) n, SUM(aprovado_por IS NOT NULL AND aprovado_por<>'') a FROM trocas_reenvios").fetchone()
            out.append({"acao": "trocas_reenvios (OMS)", "faixa": None, "executadas": r["n"], "com_aprovacao": r["a"] or 0,
                        "fonte": "trocas_reenvios"})
    except sqlite3.Error as e:
        log.warning("histórico real indisponível: %s", e)
    return out


def decisoes_sessao() -> list[dict]:
    try:
        with _db() as con:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(sim_aprovacoes)")}
            if "recomendacao" not in cols:
                return []
            rows = [dict(r) for r in con.execute("SELECT * FROM sim_aprovacoes WHERE decisao IS NOT NULL ORDER BY decidido_em")]
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        if (r.get("aprovador") or "") in VALIDADORES:
            continue
        for k in ("recomendacao", "ajuste"):
            try:
                r[k] = json.loads(r[k]) if r.get(k) else None
            except ValueError:
                r[k] = None
        out.append(r)
    return out


def _divergencias(sess: list[dict]) -> list[dict]:
    div = []
    for r in sess:
        if r.get("concordou") == 0:
            div.append({"case_id": r.get("case_id"), "aprovacao_id": r.get("aprovacao_id"), "recomendado": r.get("recomendacao"),
                        "decidido": {"decisao": r.get("decisao"), "ajuste": r.get("ajuste")},
                        "aprovador": r.get("aprovador"), "nota": (r.get("ajuste") or {}).get("nota")})
    for c in store.all():                        # nível B desfeito por uma pessoa também é divergência
        d = (c.decisao or {}).get("desfeito")
        if d and not (c.grafo or {}).get("validacao"):
            div.append({"case_id": c.id, "recomendado": {"acao": c.decisao.get("acao"), "valor": c.decisao.get("valor"),
                                                         "decisao_sugerida": "executar (nível B)"},
                        "decidido": {"decisao": "desfazer"}, "aprovador": d.get("por"), "nota": None})
    return div


def gerar_sugestoes() -> list[dict]:
    """Sobe o limite de uma ação quando os supervisores reais aprovaram 100% da faixa logo acima (amostra mínima) e
    nenhuma pessoa negou nessa faixa na sessão. Um degrau por vez."""
    _carregar_registro()
    pol = politica.carregar()
    hist = historico_real()
    sess = decisoes_sessao()
    novas = []
    for acao in ACAO_REAL:
        lim = float((pol.get("acoes", {}).get(acao) or {}).get("limite_papel_rs") or 0)
        if not lim:
            continue
        faixa = next((h for h in hist if h["acao"] == acao and h["faixa"] and h["faixa"][0] == lim), None)
        if not faixa or faixa["faixa"][1] is None or faixa["executadas"] < MIN_AMOSTRA:
            continue
        if faixa["com_aprovacao"] != faixa["executadas"]:
            continue
        lo, hi = faixa["faixa"]
        na_faixa = [r for r in sess if r.get("acao_proposta") == acao and lo < float(r.get("valor") or 0) <= hi]
        negadas = [r for r in na_faixa if r.get("decisao") == "negar"]
        if negadas:
            continue
        sid = f"S-{acao}-{int(lo)}-{int(hi)}"
        ev = (f"Supervisores aprovaram {faixa['com_aprovacao']} de {faixa['executadas']} {NOME[acao]} entre "
              f"{_brl(lo)} e {_brl(hi)} (base real)")
        if na_faixa:
            ev += f", e {len(na_faixa)} de {len(na_faixa)} na sessão"
        sug = {"id": sid, "regra": f"acoes.{acao}.limite_papel_rs", "de": lim, "para": float(hi), "evidencia": ev,
               "ressalva": "A base registra só ações executadas: recusas de supervisor não aparecem nela.",
               "impacto": f"{NOME[acao].capitalize()} entre {_brl(lo)} e {_brl(hi)} com veracidade ≥ 70 deixam de pedir "
                          "aprovação e passam a nível B (executa, avisa o supervisor e pode desfazer em 15 min).",
               "status": "proposta", "criada_em": now(), "politica_base": pol.get("versao")}
        atual = SUGESTOES.get(sid)
        if atual and atual.get("status") != "proposta":
            sug = {**sug, **{k: atual[k] for k in atual if k in ("status", "validacao", "aplicada", "criada_em")}}
        SUGESTOES[sid] = sug
        novas.append(sug)
    _salvar()
    return list(SUGESTOES.values())


def resumo() -> dict:
    sess = decisoes_sessao()
    com_rec = [r for r in sess if r.get("concordou") is not None]
    por_nivel: dict[str, dict] = {}
    for r in com_rec:
        n = por_nivel.setdefault(r.get("nivel") or "?", {"total": 0, "concordou": 0})
        n["total"] += 1
        n["concordou"] += int(bool(r["concordou"]))
    for n in por_nivel.values():
        n["concordancia_pct"] = round(100 * n["concordou"] / n["total"], 1) if n["total"] else None
    total = len(com_rec)
    return {
        "total_decisoes": total,
        "concordancia_pct": round(100 * sum(int(bool(r["concordou"])) for r in com_rec) / total, 1) if total else None,
        "por_nivel": por_nivel,
        "divergencias": _divergencias(sess),
        "sugestoes": gerar_sugestoes(),
        "historico_real": historico_real(),
        "politica_versao": politica.versao(),
    }


# ------------------------------------------------------------------ validação (job)

def _politica_proposta(sug: dict) -> dict:
    pol = copy.deepcopy(politica.carregar())
    alvo = pol
    *caminho, chave = sug["regra"].split(".")
    for k in caminho:
        alvo = alvo.setdefault(k, {})
    alvo[chave] = sug["para"]
    pol["versao"] = f"{pol.get('versao')}+{sug['id']}"
    return pol


def _script():
    p = config.ROOT / "scripts" / "clientes_sinteticos.py"
    spec = importlib.util.spec_from_file_location("goxys_clientes_sinteticos", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def validar(sid: str) -> dict:
    _carregar_registro()
    sug = SUGESTOES.get(sid)
    if sug is None:
        raise KeyError(sid)
    jid = f"JOB{int(time.time()) % 100000:05d}{next(_job_seq):02d}"
    job = {"job_id": jid, "sugestao_id": sid, "status": "rodando", "iniciado_em": now(), "feitos": 0, "total": 0,
           "placar": None, "erros": [], "politica_versao": f"{politica.versao()}+{sid}"}
    JOBS[jid] = job
    sug["status"] = "validando"
    sug["validacao"] = {"job_id": jid}
    _salvar()
    t = asyncio.create_task(_rodar_job(job, sug))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return job


async def _evento(job: dict) -> None:
    await bus.publish("aprendizado.job", {k: job[k] for k in ("job_id", "sugestao_id", "status", "feitos", "total",
                                                                 "placar", "erros")})


async def _rodar_job(job: dict, sug: dict) -> None:
    ids: list[str] = []
    try:
        politica.OVERRIDES[sug["id"]] = _politica_proposta(sug)
        mod = _script()
        P = await asyncio.to_thread(lambda: mod.personas(mod.Base()))
        job["total"] = len(P)
        await _evento(job)
        args = SimpleNamespace(timeout=300, auto_aprovar=True)
        sem = asyncio.Semaphore(3)
        async with httpx.AsyncClient(base_url=config.SYSTEMS_BASE_URL, timeout=60,
                                     headers={"X-Goxy-Politica": sug["id"]}) as cli:
            async def um(p):
                r = await mod._rodar(cli, p, args, sem)
                job["feitos"] += 1
                if r.get("case_id"):
                    ids.append(r["case_id"])
                await _evento(job)
                return r
            res = await asyncio.gather(*(um(p) for p in P))
        ok = sum(1 for r in res if r.get("ok"))
        job["placar"] = {"acertos": ok, "total": len(res)}
        job["erros"] = [{"persona": r["persona"], "falhas": r.get("falhas"), "obtido": r.get("obtido")}
                        for r in res if not r.get("ok")]
        job["status"] = "concluido"
        sug["status"] = "validada" if ok == len(res) and res else "reprovada"
    except Exception as e:
        log.exception("validação %s falhou", job["job_id"])
        job["status"], job["erro"] = "falhou", f"{type(e).__name__}: {e}"
        sug["status"] = "reprovada"
        job["erros"].append({"persona": None, "falhas": [job["erro"]]})
    finally:
        politica.OVERRIDES.pop(sug["id"], None)
        job["concluido_em"] = now()
        sug["validacao"] = {"job_id": job["job_id"], "placar": job["placar"], "erros": job["erros"], "em": now()}
        _salvar()
        await _evento(job)
        await _limpar_casos(ids)


async def _limpar_casos(ids: list[str]) -> None:
    """Os casos da validação não poluem a fila nem as métricas."""
    from . import grafo
    for cid in ids:
        store._cases.pop(cid, None)
    try:
        with sqlite3.connect(config.DB_PATH) as con:
            con.executemany("DELETE FROM goxys_cases WHERE id=?", [(i,) for i in ids])
    except sqlite3.Error:
        pass
    await grafo.apagar_threads(ids)


# ------------------------------------------------------------------ aplicar

def _nova_versao(v: str) -> str:
    m = re.search(r"-r(\d+)$", v or "")
    return re.sub(r"-r\d+$", f"-r{int(m.group(1)) + 1}", v) if m else f"{v}-r2"


async def aplicar(sid: str, por: str) -> dict:
    _carregar_registro()
    sug = SUGESTOES.get(sid)
    if sug is None:
        raise KeyError(sid)
    if sug.get("status") != "validada":
        raise ValueError(f"a sugestão está '{sug.get('status')}'; só uma sugestão validada (todos os sintéticos certos) pode ser aplicada")
    path: Path = config.POLICY_PATH
    texto = path.read_text(encoding="utf-8")
    atual = yaml.safe_load(texto)
    anterior = str(atual.get("versao"))
    nova = _nova_versao(anterior)
    hist = path.parent / "historico"
    hist.mkdir(exist_ok=True)
    shutil.copy(path, hist / f"autonomia-{anterior}.yaml")

    novo_txt = re.sub(r'^versao:.*$', f'versao: "{nova}"   # {nova.split("-")[-1]}: {sug["regra"]} {sug["de"]:g} → '
                                      f'{sug["para"]:g} ({sug["id"]}, validada nos sintéticos, aplicada por {por})',
                      texto, count=1, flags=re.M)
    *caminho, chave = sug["regra"].split(".")
    if caminho[:1] == ["acoes"] and len(caminho) == 2:
        novo_txt, n = re.subn(rf"^(\s*{re.escape(caminho[1])}:\s*\{{[^\n}}]*?{re.escape(chave)}:\s*)[\d.]+",
                              lambda m: f"{m.group(1)}{sug['para']:g}", novo_txt, count=1, flags=re.M)
    else:
        n = 0
    conferido = yaml.safe_load(novo_txt) if n else None
    alvo = conferido
    for k in caminho:
        alvo = (alvo or {}).get(k) if isinstance(alvo, dict) else None
    if not n or not isinstance(alvo, dict) or float(alvo.get(chave)) != float(sug["para"]):
        pol = _politica_proposta(sug)            # fallback: regrava o YAML inteiro (perde comentários)
        pol["versao"] = nova
        novo_txt = yaml.safe_dump(pol, allow_unicode=True, sort_keys=False)
    path.write_text(novo_txt, encoding="utf-8")
    politica._carregar.cache_clear()
    assert politica.versao() == nova
    sug.update({"status": "aplicada", "aplicada": {"por": por, "em": now(), "versao": nova, "anterior": anterior}})
    _salvar()
    await bus.publish("policy.updated", {"versao": nova, "anterior": anterior, "regra": sug["regra"], "de": sug["de"],
                                         "para": sug["para"], "por": por, "sugestao_id": sid,
                                         "historico": str(hist / f"autonomia-{anterior}.yaml")})
    return sug
