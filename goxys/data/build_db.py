"""CSV do evento -> SQLite (data/goxys.db) + linhas DEMO dos 5 casos de demo.

    cd goxys && .venv/bin/python -m data.build_db          # (re)constrói
    .venv/bin/python data/build_db.py --se-faltar          # só constrói se o banco não existe/está incompleto

Idempotente: constrói num arquivo temporário e troca atomicamente. Roda em poucos segundos.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

ATENDIMENTO = config.DATA_RAW / "atendimento"
TRANSPORTADORAS = config.DATA_RAW / "transportes" / "transportadoras.csv"
DEMO_CASES = HERE / "demo_cases.json"
SCHEMA_VERSION = "goxys-db-3"

INDEX_COLS = ("order_id", "customer_id", "conversation_id", "hash_perceptual", "pedido_ref",
              "codigo_rastreio", "order_id_original", "transacao_id")

_INT = re.compile(r"^-?(0|[1-9]\d*)$")
_FLOAT = re.compile(r"^-?(0|[1-9]\d*)\.\d+$")

SIM_TABLES = {
    "sim_chamados": """chamado_id TEXT PRIMARY KEY, conversation_id TEXT, customer_id TEXT, order_id TEXT,
        canal TEXT, assunto TEXT, status TEXT, criado_em TEXT, atualizado_em TEXT, historico TEXT""",
    "sim_trocas": """solicitacao_id TEXT PRIMARY KEY, order_id TEXT, sku TEXT, tipo TEXT, motivo TEXT,
        quantidade INTEGER, pedido_reposicao_id TEXT, custo_total REAL, status TEXT, criado_em TEXT,
        solicitado_por TEXT, idempotency_key TEXT""",
    "sim_chamados_transportadora": """chamado_id TEXT PRIMARY KEY, protocolo TEXT, order_id TEXT, transportadora TEXT,
        codigo_rastreio TEXT, motivo TEXT, prazo_resposta TEXT, status TEXT, criado_em TEXT, aberto_por TEXT,
        idempotency_key TEXT""",
    "sim_reembolsos": """reembolso_id TEXT PRIMARY KEY, order_id TEXT, valor REAL, motivo TEXT, aprovacao_id TEXT,
        status TEXT, criado_em TEXT, solicitado_por TEXT, idempotency_key TEXT UNIQUE, resposta TEXT""",
    "sim_aprovacoes": """aprovacao_id TEXT PRIMARY KEY, case_id TEXT, titulo TEXT, resumo TEXT, nivel TEXT, valor REAL,
        acao_proposta TEXT, opcoes TEXT, evidencias TEXT, prazo_s INTEGER, status TEXT, criado_em TEXT,
        expira_em TEXT, decisao TEXT, aprovador TEXT, canal TEXT, ajuste TEXT, decidido_em TEXT,
        tempo_resposta_s REAL, solicitante TEXT""",
    "sim_mensagens": """message_id TEXT PRIMARY KEY, canal TEXT, customer_id TEXT, conversation_id TEXT, case_id TEXT,
        texto TEXT, enviado_em TEXT, telegram_chat_id TEXT, autor TEXT""",
    "sim_eventos": """evento_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, case_id TEXT, agente TEXT, metodo TEXT,
        rota TEXT, sistema TEXT, acao TEXT, parametros TEXT, status_http INTEGER, resultado TEXT,
        idempotency_key TEXT""",
}


def _col_type(values: list[str]) -> str:
    vals = [v for v in values if v != ""]
    if not vals:
        return "TEXT"
    if all(_INT.match(v) for v in vals):
        return "INTEGER"
    if all(_INT.match(v) or _FLOAT.match(v) for v in vals):
        return "REAL"
    return "TEXT"


def _conv(v: str, t: str):
    if v == "":
        return None
    if t == "INTEGER":
        return int(v)
    if t == "REAL":
        return float(v)
    return v


def load_csv(con: sqlite3.Connection, table: str, path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    types = [_col_type([r[i] if i < len(r) else "" for r in data]) for i in range(len(header))]
    cols = ", ".join(f'"{h}" {t}' for h, t in zip(header, types))
    con.execute(f'CREATE TABLE "{table}" ({cols})')
    ph = ", ".join("?" * len(header))
    con.executemany(f'INSERT INTO "{table}" VALUES ({ph})',
                    ([_conv(r[i] if i < len(r) else "", types[i]) for i in range(len(header))] for r in data))
    for c in header:
        if c in INDEX_COLS:
            con.execute(f'CREATE INDEX "ix_{table}_{c}" ON "{table}"("{c}")')
    if table == "pedidos":
        con.execute('CREATE UNIQUE INDEX ux_pedidos_order ON pedidos(order_id)')
    return len(data)


def _insert(con: sqlite3.Connection, table: str, row: dict) -> None:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    unknown = set(row) - set(cols)
    if unknown:
        raise ValueError(f"{table}: colunas desconhecidas {unknown}")
    keys = list(row)
    con.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + k + chr(34) for k in keys)}) '
                f'VALUES ({", ".join("?" * len(keys))})', [row[k] for k in keys])


# Pedido DEMO do caso 4 (a base não tem pedido > R$ 500 entregue na última semana). Cliente real CH384938.
DEMO_PEDIDO_4 = {
    "pedidos": [{
        "order_id": "GBH990004", "customer_id": "CH384938", "marca": "Lescent", "data_pedido": "2026-10-24",
        "valor": 689.70, "sku": "LSC-5002",
        "descricao_produto": "Perfume Feminino Oriental Âmbar 100ml + Brinde Necessaire GoBeauté",
        "personalizacao": None, "transportadora": "ViaLog", "uf": "SP", "status_entrega": "entregue",
        "data_separacao": "2026-10-26 10:12:44", "cd_separacao": "CD2", "maquina_personalizacao": None,
        "data_entrega": "2026-10-30", "lote_produto": "LT2610-A", "data_hora_pedido": "2026-10-24 21:07:33",
        "canal_venda": "app", "quantidade_itens": 4, "valor_produtos": 689.70, "desconto": 0.0, "cupom": None,
        "frete_cobrado": 0.0, "forma_pagamento": "cartao_credito", "parcelas": 4, "status_pagamento": "aprovado",
        "data_prometida": "2026-10-30", "codigo_rastreio": "VL99000400417", "numero_nota_fiscal": "000199004",
        "cep_entrega": "14005-023", "cidade_entrega": "Ribeirão Preto"}],
    "itens_pedido": [
        {"item_id": "GBH990004-1", "order_id": "GBH990004", "linha": 1, "sku": "LSC-5002",
         "descricao": "Perfume Feminino Oriental Âmbar 100ml", "tipo_item": "venda", "quantidade": 2,
         "preco_unitario": 259.90, "desconto_rateado": 0.0, "valor_total": 519.80, "lote": "LT2610-A"},
        {"item_id": "GBH990004-2", "order_id": "GBH990004", "linha": 2, "sku": "LSC-5004",
         "descricao": "Perfume Unissex Cítrico Neroli 50ml", "tipo_item": "venda", "quantidade": 1,
         "preco_unitario": 169.90, "desconto_rateado": 0.0, "valor_total": 169.90, "lote": "LT2610-C"},
        {"item_id": "GBH990004-3", "order_id": "GBH990004", "linha": 3, "sku": "GBT-9001",
         "descricao": "Necessaire GoBeauté", "tipo_item": "brinde", "quantidade": 1,
         "preco_unitario": 0.0, "desconto_rateado": 0.0, "valor_total": 0.0},
    ],
    "pagamentos_gateway": [{
        "transacao_id": "TRX99000004", "order_id": "GBH990004", "customer_id": "CH384938",
        "metodo": "cartao_credito", "bandeira": "Visa", "cartao_final": "8812", "parcelas": 4, "valor": 689.70,
        "status": "capturada", "data_hora_criacao": "2026-10-24 21:07:33",
        "data_hora_confirmacao": "2026-10-24 21:07:51", "nsu": "099000417", "codigo_autorizacao": "7KQ2DM",
        "adquirente": "PagNet", "valor_estornado": 0.0}],
    "rastreio_portais_transportadoras": [
        {"portal": "ViaLog", "pedido_ref": "GBH990004", "data_hora_evento": e[0], "status": e[1], "local": e[2],
         "previsao_entrega": "2026-10-30", "codigo_rastreio": "VL99000400417"}
        for e in [("2026-10-27 19:40", "coletado", "CD2"),
                  ("2026-10-28 06:15", "em_transferencia", "centro de distribuição da transportadora"),
                  ("2026-10-29 23:02", "chegada_unidade_destino", "unidade SP"),
                  ("2026-10-30 07:48", "saiu_para_entrega", "unidade SP"),
                  ("2026-10-30 14:21", "entregue", "destino SP")]],
}


def insert_demo_rows(con: sqlite3.Connection) -> int:
    """Linhas DEMO: o pedido do caso 4 e os anexos DEMO-* de demo_cases.json (anexos reais não são duplicados)."""
    n = 0
    for table, rows in DEMO_PEDIDO_4.items():
        for row in rows:
            _insert(con, table, row)
            n += 1
    if DEMO_CASES.exists():
        for caso in json.loads(DEMO_CASES.read_text(encoding="utf-8")):
            for i, a in enumerate(caso.get("anexos") or [], 1):
                if not str(a.get("anexo_id", "")).startswith("DEMO"):
                    continue  # anexo real da base (ex.: caso 2 = IMG000267): já está em anexos_conversas
                _insert(con, "anexos_conversas", {
                    "anexo_id": a["anexo_id"], "conversation_id": caso["conversation_id"],
                    "message_id": f"DEMO-MSG{caso['n']:02d}{i}", "order_id": caso["order_id"],
                    "customer_id": caso["customer_id"], "canal": caso["canal"], "enviado_em": a.get("enviado_em"),
                    "tipo": a.get("tipo"), "hash_perceptual": a.get("hash_perceptual"),
                    "exif_data_captura": a.get("exif_data_captura") or None,
                    "exif_modelo_camera": a.get("exif_modelo_camera") or None,
                    "exif_software": a.get("exif_software") or None,
                    "descricao_visual": a.get("descricao_visual")})
                n += 1
    return n


def _fingerprint() -> str:
    h = hashlib.sha1(SCHEMA_VERSION.encode())
    h.update(Path(__file__).read_bytes())
    if DEMO_CASES.exists():
        h.update(DEMO_CASES.read_bytes())
    return h.hexdigest()[:16]


def build(db_path: Path = config.DB_PATH, verbose: bool = True) -> Path:
    t0 = time.time()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    total = 0
    for path in sorted(ATENDIMENTO.glob("*.csv")):
        total += load_csv(con, path.stem, path)
    total += load_csv(con, "transportadoras", TRANSPORTADORAS)
    for name, ddl in SIM_TABLES.items():
        con.execute(f"CREATE TABLE {name} ({ddl})")
    con.execute("CREATE INDEX ix_sim_eventos_case ON sim_eventos(case_id)")
    con.execute("CREATE INDEX ix_sim_aprov_status ON sim_aprovacoes(status)")
    con.execute("CREATE INDEX ix_sim_msg_conv ON sim_mensagens(conversation_id)")
    con.execute("CREATE TABLE IF NOT EXISTS goxys_cases (id TEXT PRIMARY KEY, snapshot TEXT, atualizado_em TEXT)")
    demo = insert_demo_rows(con)
    con.execute("CREATE TABLE _meta (chave TEXT PRIMARY KEY, valor TEXT)")
    con.execute("INSERT INTO _meta VALUES ('schema', ?), ('construido_em', datetime('now'))", (_fingerprint(),))
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    if verbose:
        print(f"goxys.db pronto: {total} linhas dos CSVs + {demo} linhas DEMO em {time.time() - t0:.1f}s -> {db_path}")
    return db_path


def is_ready(db_path: Path = config.DB_PATH) -> bool:
    if not Path(db_path).exists():
        return False
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT valor FROM _meta WHERE chave='schema'").fetchone()
            return bool(row and row[0] == _fingerprint())
        finally:
            con.close()
    except sqlite3.Error:
        return False


def ensure(db_path: Path = config.DB_PATH) -> Path:
    if not is_ready(db_path):
        build(db_path)
    return Path(db_path)


if __name__ == "__main__":
    if "--se-faltar" in sys.argv:
        ensure()
    else:
        build()
