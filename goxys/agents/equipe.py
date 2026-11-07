"""Equipe humana (equipe_atendimento): quem pode receber um caso e quem o Goxy sugere.

Nível D → um analista; nível C → um supervisor. O supervisor não aprova a própria ação, então a
sugestão nunca é o supervisor que já está com o caso (ou que pediu a ação).
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache

from core import config
from core.state import Case, store

CAMPOS = ("colaborador_id", "nome", "cargo", "nivel", "canais_atendidos", "supervisor_id", "status")


@lru_cache(maxsize=4)
def _carregar(db: str) -> tuple[dict, ...]:
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        with con:
            rows = con.execute("SELECT * FROM equipe_atendimento WHERE status='ativo' ORDER BY colaborador_id").fetchall()
        return tuple({k: r[k] for k in CAMPOS if k in r.keys()} for r in rows)
    except sqlite3.Error:
        return ()


def listar() -> list[dict]:
    out = []
    for p in _carregar(str(config.DB_PATH)):
        p = dict(p)
        canais = p.get("canais_atendidos") or ""
        p["canais_atendidos"] = [c.strip() for c in canais.split(";") if c.strip()] if isinstance(canais, str) else canais
        out.append(p)
    return out


def get(colaborador_id: str | None) -> dict | None:
    return next((p for p in listar() if p["colaborador_id"] == colaborador_id), None)


def _carga(colaborador_id: str) -> int:
    n = 0
    for c in store.all():
        if c.status in ("resolvido", "rejeitado", "erro"):
            continue
        if (c.responsavel or {}).get("colaborador_id") == colaborador_id:
            n += 1
        if (c.sugestao_responsavel or {}).get("colaborador_id") == colaborador_id and not c.responsavel:
            n += 1
    return n


def sugerir(case: Case, nivel: str) -> dict | None:
    if nivel not in ("C", "D"):
        return None
    cargo = "analista" if nivel == "D" else "supervisor"
    excluir = {(case.responsavel or {}).get("colaborador_id"), (case.aprovacao or {}).get("solicitante")}
    candidatos = [p for p in listar() if p["cargo"] == cargo and p["colaborador_id"] not in excluir
                  and (not p.get("canais_atendidos") or case.canal not in ("whatsapp", "email", "reclame_aqui")
                       or case.canal in p["canais_atendidos"])]
    if not candidatos:
        candidatos = [p for p in listar() if p["cargo"] == cargo and p["colaborador_id"] not in excluir]
    if not candidatos:
        return None
    p = min(candidatos, key=lambda x: (_carga(x["colaborador_id"]), x["colaborador_id"]))
    motivo = ("Nível D: caso crítico, um analista assume e decide" if nivel == "D" else
              "Nível C: precisa de aprovação de supervisor (que não seja quem pediu a ação)")
    return {"colaborador_id": p["colaborador_id"], "nome": p["nome"], "cargo": p["cargo"], "motivo": motivo}
