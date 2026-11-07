"""Sistemas simulados do Goxy (CONTRACTS §2 / SPEC §19).

ERP, chamados, logística, financeiro, aprovações, mensageria, log de eventos e o portal web da
TransNorte (sem API). Tudo lê/escreve o SQLite construído por `data/build_db.py`.

Exporta `routers: list[APIRouter]` e `async def startup()`.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from core import config
from core.bus import bus

DB_PATH: Path = config.DB_PATH          # os testes trocam por uma cópia temporária
NOW_ENV = datetime.fromisoformat(config.NOW_ISO)
LIMITE_REEMBOLSO_SEM_APROVACAO = 500.0
FERIADOS = {date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15),
            date(2026, 11, 20), date(2026, 12, 25), date(2027, 1, 1)}
SEM_API = {"TransNorte"}
PORTAL_USUARIO, PORTAL_SENHA = "goxys", "hackathon"
_write_lock = threading.Lock()


# ------------------------------------------------------------------ infraestrutura

def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = lambda c, r: {d[0]: v for d, v in zip(c.description, r)}
    return con


def _q(sql: str, params: tuple | list = ()) -> list[dict]:
    con = _con()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _q1(sql: str, params: tuple | list = ()) -> Optional[dict]:
    rows = _q(sql, params)
    return rows[0] if rows else None


def _exec(sql: str, params: tuple | list = ()) -> int:
    with _write_lock:
        con = _con()
        try:
            cur = con.execute(sql, params)
            con.commit()
            return cur.lastrowid
        finally:
            con.close()


def _insert(table: str, row: dict) -> None:
    cols = list(row)
    _exec(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
          [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row.values()])


def _agora() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _novo_id(prefixo: str) -> str:
    return f"{prefixo}-{_agora():%y%m%d}-{secrets.token_hex(3).upper()}"


def _erro(status: int, codigo: str, mensagem: str, **extra) -> JSONResponse:
    return JSONResponse({"erro": codigo, "mensagem": mensagem, **extra}, status_code=status)


def _parse(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).strip())
    except ValueError:
        return None


def _dias_uteis(inicio: date, fim: date) -> int:
    n, d = 0, inicio
    while d < fim:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in FERIADOS:
            n += 1
    return n


def _case_id(request: Request, body: Optional[dict] = None) -> Optional[str]:
    return ((body or {}).get("case_id") or request.headers.get("X-Caso")
            or request.headers.get("X-Case-Id") or request.query_params.get("case_id"))


def _log(request: Request, sistema: str, acao: str, parametros: Any, status: int, resultado: Any,
         case_id: Optional[str] = None, idem: Optional[str] = None) -> None:
    """Log de ferramentas (sim_eventos): toda escrita passa por aqui, com quem pediu."""
    _insert("sim_eventos", {
        "ts": _iso(_agora()), "case_id": case_id, "agente": request.headers.get("X-Agente"),
        "metodo": request.method, "rota": request.url.path, "sistema": sistema, "acao": acao,
        "parametros": parametros, "status_http": status, "resultado": resultado, "idempotency_key": idem,
    })


def _idem_anterior(request: Request) -> tuple[Optional[str], Optional[Any]]:
    """Idempotency-Key genérica para escritas: mesma rota + mesma chave → mesma resposta."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None, None
    row = _q1("SELECT resultado FROM sim_eventos WHERE rota=? AND idempotency_key=? AND status_http<300 "
              "ORDER BY evento_id LIMIT 1", (request.url.path, key))
    if row:
        res = json.loads(row["resultado"])
        if isinstance(res, dict):
            res["idempotente"] = True
        return key, res
    return key, None


class Corpo(BaseModel):
    model_config = ConfigDict(extra="allow")


def _pedido(order_id: str) -> Optional[dict]:
    return _q1("SELECT * FROM pedidos WHERE order_id=?", (order_id,))


# ------------------------------------------------------------------ ERP

erp = APIRouter(prefix="/api/erp", tags=["ERP"])


_BUSCA_COLS = ("p.order_id, p.customer_id, c.nome AS cliente_nome, p.descricao_produto AS produto, p.sku, p.valor, "
               "p.status_entrega, p.data_pedido, c.canal_preferido, p.marca, p.cidade_entrega")
_PRIORIDADE = "CASE p.status_entrega WHEN 'entregue' THEN 0 WHEN 'em_transito' THEN 1 ELSE 2 END"


def _fmt_busca(r: dict) -> dict:
    r = dict(r)
    r["produto"] = (r.get("produto") or "").split(" + ")[0]
    return {k: r.get(k) for k in ("order_id", "customer_id", "cliente_nome", "produto", "sku", "valor", "status_entrega",
                                  "data_pedido", "canal_preferido")}


@erp.get("/pedidos")
def buscar_pedidos(q: str = "", limite: int = Query(20, ge=1, le=100)):
    """Busca pedidos REAIS (simulador): order_id, nome do cliente, produto ou cidade. Prioriza entregues e em trânsito
    recentes. Sem `q`: pedidos variados e recentes, começando pelos 5 da demo."""
    hoje = NOW_ENV.strftime("%Y-%m-%d")
    base = (f"SELECT {_BUSCA_COLS} FROM pedidos p LEFT JOIN clientes c ON c.customer_id = p.customer_id "
            "WHERE p.data_pedido <= ?")
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        rows = _q(base + " AND (p.order_id LIKE ? OR c.nome LIKE ? OR p.descricao_produto LIKE ? OR p.cidade_entrega LIKE ? "
                  "OR p.order_id IN (SELECT order_id FROM itens_pedido WHERE descricao LIKE ?)) "
                  f"ORDER BY (p.order_id = ?) DESC, {_PRIORIDADE}, p.data_pedido DESC LIMIT ?",
                  (hoje, like, like, like, like, like, q.upper(), limite))
        return [_fmt_busca(r) for r in rows]
    out: list[dict] = []
    vistos: set[str] = set()
    demo = config.DATA_DIR / "demo_cases.json"
    if demo.exists():
        try:
            ids = [d.get("order_id") for d in json.loads(demo.read_text(encoding="utf-8")) if d.get("order_id")]
        except ValueError:
            ids = []
        for oid in ids:
            r = _q1(f"SELECT {_BUSCA_COLS} FROM pedidos p LEFT JOIN clientes c ON c.customer_id = p.customer_id "
                    "WHERE p.order_id=?", (oid,))
            if r and r["order_id"] not in vistos:
                out.append(_fmt_busca(r))
                vistos.add(r["order_id"])
    recentes = _q(base + f" AND p.data_pedido >= ? ORDER BY {_PRIORIDADE}, p.data_pedido DESC LIMIT 600",
                  (hoje, (NOW_ENV - timedelta(days=45)).strftime("%Y-%m-%d")))
    grupos: dict[tuple, list[dict]] = {}
    for r in recentes:                       # variedade: rodízio por marca × status
        grupos.setdefault((r.get("marca"), r.get("status_entrega")), []).append(r)
    filas = list(grupos.values())
    while len(out) < limite and any(filas):
        for fila in filas:
            while fila and fila[0]["order_id"] in vistos:
                fila.pop(0)
            if fila and len(out) < limite:
                r = fila.pop(0)
                out.append(_fmt_busca(r))
                vistos.add(r["order_id"])
    return out[:limite]


@erp.get("/pedidos/{order_id}")
def get_pedido(order_id: str):
    p = _pedido(order_id)
    if not p:
        return _erro(404, "pedido_nao_encontrado", f"Pedido {order_id} não existe no ERP")
    p["itens"] = _q("SELECT * FROM itens_pedido WHERE order_id=? ORDER BY linha", (order_id,))
    p["pagamentos"] = _q("SELECT transacao_id, metodo, bandeira, cartao_final, parcelas, valor, status, "
                         "data_hora_criacao, data_hora_confirmacao, adquirente, valor_estornado "
                         "FROM pagamentos_gateway WHERE order_id=? ORDER BY data_hora_criacao", (order_id,))
    return p


def _mascarar_cpf(cpf: Optional[str]) -> Optional[str]:
    if not cpf:
        return cpf
    d = re.sub(r"\D", "", cpf)
    return f"***.{d[3:6]}.{d[6:9]}-**" if len(d) == 11 else "***"


def _mascarar_tel(tel: Optional[str]) -> Optional[str]:
    if not tel:
        return tel
    d = re.sub(r"\D", "", tel)
    return f"({d[:2]}) *****-{d[-4:]}" if len(d) >= 10 else "***"


QUEIXAS_EXCLUIDAS = ("Dúvida", "Elogio", "Promoção")


def _risco_cliente(customer_id: str, c: dict) -> dict:
    corte90 = (NOW_ENV - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    corte12m = (NOW_ENV - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    conversas = _q(
        "SELECT conversation_id FROM historico_conversas WHERE customer_id=? AND data_abertura>=? "
        f"AND COALESCE(tag_agente,'') NOT IN ({','.join('?' * len(QUEIXAS_EXCLUIDAS))}) "
        "UNION SELECT conversation_id FROM fila_aberta WHERE customer_id=? AND data_abertura>=?",
        (customer_id, corte90, *QUEIXAS_EXCLUIDAS, customer_id, corte90))
    ra90 = _q1("SELECT COUNT(*) n FROM reclame_aqui WHERE customer_id=? AND data_publicacao>=?",
               (customer_id, corte90))["n"]
    avarias = _q1("SELECT COUNT(*) n FROM trocas_reenvios WHERE customer_id=? AND data_solicitacao>=? AND "
                  "motivo_registrado IN ('avaria','defeito_produto','item_faltante')", (customer_id, corte12m))["n"]
    # foto deste cliente cujo hash já tinha sido enviado antes por outro pedido
    reuso = _q("""SELECT a.anexo_id, a.hash_perceptual FROM anexos_conversas a
                  WHERE a.customer_id=? AND a.hash_perceptual IS NOT NULL AND EXISTS (
                    SELECT 1 FROM anexos_conversas b WHERE b.hash_perceptual=a.hash_perceptual
                    AND b.order_id<>a.order_id AND b.enviado_em<a.enviado_em)""", (customer_id,))
    motivos = []
    if (c.get("n_trocas_12m") or 0) >= 3:
        motivos.append(f"{c['n_trocas_12m']} trocas/reenvios em 12 meses")
    if avarias >= 2:
        motivos.append(f"{avarias} reenvios por avaria/defeito/falta em 12 meses")
    if reuso:
        motivos.append(f"enviou foto já usada em outro pedido ({', '.join(r['anexo_id'] for r in reuso)})")
    return {"flag_risco": bool(motivos), "motivos_risco": motivos, "qtd_reclamacoes_90d": len(conversas),
            "qtd_reclame_aqui_90d": ra90, "reenvios_avaria_12m": avarias}


@erp.get("/clientes/{customer_id}")
def get_cliente(customer_id: str):
    c = _q1("SELECT * FROM clientes WHERE customer_id=?", (customer_id,))
    if not c:
        return _erro(404, "cliente_nao_encontrado", f"Cliente {customer_id} não existe no CRM")
    c["cpf"] = _mascarar_cpf(c.get("cpf"))
    c["telefone"] = _mascarar_tel(c.get("telefone"))
    c.update(_risco_cliente(customer_id, c))
    return c


@erp.get("/clientes/{customer_id}/historico")
def get_historico(customer_id: str):
    if not _q1("SELECT 1 FROM clientes WHERE customer_id=?", (customer_id,)):
        return _erro(404, "cliente_nao_encontrado", f"Cliente {customer_id} não existe no CRM")
    pedidos = _q("SELECT order_id, marca, data_pedido, valor, sku, descricao_produto, personalizacao, transportadora, "
                 "uf, status_entrega, data_prometida, data_entrega, codigo_rastreio FROM pedidos WHERE customer_id=? "
                 "ORDER BY data_pedido DESC", (customer_id,))
    conversas = _q("SELECT conversation_id, order_id, marca, canal, data_abertura, tag_agente, status, assunto, "
                   "'historico' AS origem FROM historico_conversas WHERE customer_id=? "
                   "UNION ALL SELECT conversation_id, order_id, marca, canal, data_abertura, NULL, status, assunto, "
                   "'fila_aberta' FROM fila_aberta WHERE customer_id=? ORDER BY data_abertura DESC",
                   (customer_id, customer_id))
    trocas = _q("SELECT solicitacao_id, conversation_id, order_id_original AS order_id, pedido_reposicao_id, tipo, "
                "motivo_registrado AS motivo, sku_enviado AS sku, data_solicitacao, status, aprovado_por, custo_total "
                "FROM trocas_reenvios WHERE customer_id=? ORDER BY data_solicitacao DESC", (customer_id,))
    ids = [p["order_id"] for p in pedidos]
    if ids:
        ph = ",".join("?" * len(ids))
        trocas += _q(f"SELECT solicitacao_id, NULL AS conversation_id, order_id, pedido_reposicao_id, tipo, motivo, sku, "
                     f"criado_em AS data_solicitacao, status, NULL AS aprovado_por, custo_total, 'goxys' AS origem "
                     f"FROM sim_trocas WHERE order_id IN ({ph})", ids)
        estornos = _q(f"SELECT estorno_id, order_id, tipo, valor, motivo, aprovado_por, data_hora_solicitacao, status "
                      f"FROM estornos WHERE order_id IN ({ph}) ORDER BY data_hora_solicitacao DESC", ids)
        estornos += _q(f"SELECT reembolso_id AS estorno_id, order_id, 'reembolso' AS tipo, valor, motivo, aprovacao_id "
                       f"AS aprovado_por, criado_em AS data_hora_solicitacao, status FROM sim_reembolsos "
                       f"WHERE order_id IN ({ph})", ids)
    else:
        estornos = []
    anexos = _q("SELECT * FROM anexos_conversas WHERE customer_id=? ORDER BY enviado_em DESC", (customer_id,))
    ra = _q("SELECT reclamacao_id, conversation_id, order_id, titulo, data_publicacao, status_publico, resolvido, "
            "nota_consumidor, voltaria_a_fazer_negocio FROM reclame_aqui WHERE customer_id=? "
            "ORDER BY data_publicacao DESC", (customer_id,))
    return {"customer_id": customer_id, "pedidos": pedidos, "conversas": conversas, "trocas": trocas,
            "estornos": estornos, "anexos": anexos, "reclame_aqui": ra}


def _eventos_rastreio(order_id: str) -> list[dict]:
    return _q("SELECT data_hora_evento AS data_hora, status, local, previsao_entrega FROM "
              "rastreio_portais_transportadoras WHERE pedido_ref=? ORDER BY data_hora_evento", (order_id,))


def _atraso(p: dict) -> dict:
    prom = _parse(p.get("data_prometida"))
    status = p.get("status_entrega") or ""
    hoje = NOW_ENV.date()
    if not prom or status in ("cancelado", "devolvido", "aguardando_separacao") and prom.date() >= hoje:
        return {"atrasado": False, "dias_atraso": 0, "dias_uteis_atraso": 0}
    if status == "entregue":
        ent = _parse(p.get("data_entrega"))
        ref = ent.date() if ent else prom.date()
    elif status in ("cancelado", "devolvido"):
        return {"atrasado": False, "dias_atraso": 0, "dias_uteis_atraso": 0}
    else:
        ref = hoje
    dias = (ref - prom.date()).days
    return {"atrasado": dias > 0, "dias_atraso": max(dias, 0),
            "dias_uteis_atraso": _dias_uteis(prom.date(), ref) if dias > 0 else 0}


@erp.get("/rastreio/{order_id}")
def get_rastreio(order_id: str):
    p = _pedido(order_id)
    if not p:
        return _erro(404, "pedido_nao_encontrado", f"Pedido {order_id} não existe no ERP")
    if p["transportadora"] in SEM_API:
        return _erro(501, "sem_api", f"{p['transportadora']} não tem API de rastreio", portal="/portal",
                     transportadora=p["transportadora"], codigo_rastreio=p["codigo_rastreio"])
    ev = _eventos_rastreio(order_id)
    previsao = (ev[-1]["previsao_entrega"] if ev else None) or p["data_prometida"]
    chamados = _q("SELECT chamado_id, data_abertura, motivo, status, resposta_transportadora, nova_previsao_entrega "
                  "FROM chamados_transportadora WHERE order_id=?", (order_id,))
    chamados += _q("SELECT chamado_id, criado_em AS data_abertura, motivo, status, NULL AS resposta_transportadora, "
                   "NULL AS nova_previsao_entrega FROM sim_chamados_transportadora WHERE order_id=?", (order_id,))
    return {"order_id": order_id, "transportadora": p["transportadora"], "codigo_rastreio": p["codigo_rastreio"],
            "status": p["status_entrega"], "previsao_entrega": previsao, "data_prometida": p["data_prometida"],
            "data_entrega": p["data_entrega"], **_atraso(p), "fonte": "api",
            "eventos": [{k: e[k] for k in ("data_hora", "status", "local")} for e in ev],
            "chamados_transportadora": chamados}


@erp.get("/anexos")
def get_anexos(conversation_id: Optional[str] = None, order_id: Optional[str] = None,
               hash: Optional[str] = None, customer_id: Optional[str] = None):
    filtros, params = [], []
    for col, v in (("conversation_id", conversation_id), ("order_id", order_id),
                   ("hash_perceptual", hash), ("customer_id", customer_id)):
        if v:
            filtros.append(f"{col}=?")
            params.append(v)
    if not filtros:
        return _erro(400, "filtro_obrigatorio", "Informe conversation_id, order_id, hash ou customer_id")
    return _q(f"SELECT * FROM anexos_conversas WHERE {' AND '.join(filtros)} ORDER BY enviado_em", params)


# --- perícia de foto ------------------------------------------------

AVARIA_TERMOS = ("rachad", "quebrad", "rasgad", "rasgo", "amassad", "descascad", "vazad", "vazando", "vazou",
                 "vazamento", "arrebentad", "trincad", "riscad", "borrad", "manchad", "umidade", "molhad", "furad",
                 "solto", "solta", "tort", "desbotad", "estourad", "lascad", "faltando", "falta", "danificad",
                 "errad", "diferente")
AMBIGUO_TERMOS = ("não dá para ver", "não é possível ver", "não é possível identificar", "não dá pra ver",
                  "sem dano visível", "não mostra", "não se vê", "pouco nítida", "desfocad", "escura demais")
SOFTWARE_SUSPEITO = ("photoshop", "gimp", "lightroom", "snapseed", "picsart", "facetune", "canva", "firefly",
                     "midjourney", "dall", "stable diffusion", "generativ", "remini", "faceapp", "pixelmator",
                     "inteligência artificial", " ia", "ai ", "gerad")
INDICIOS_DESCRICAO = {
    "adicionad": "elemento de dano 'adicionado' à imagem",
    "contorno nítido": "contorno nítido demais no dano (recorte/colagem)",
    "bordas nítidas": "bordas nítidas demais no dano (recorte/colagem)",
    "sombra que não": "sombra inconsistente com a iluminação da cena",
    "iluminação inconsistente": "iluminação inconsistente",
    "luz do resto": "luz do dano não acompanha o resto da foto",
    "artefato": "artefatos de geração/edição",
    "textura repetida": "textura repetida (padrão de IA)",
    "montagem": "aparência de montagem",
}
CATEGORIAS = {
    "case": ("capinha", "case", "capa "),
    "copo": ("copo",),
    "garrafa": ("garrafa",),
    "necessaire": ("necessaire",),
    "bolsa": ("bolsa", "tote", "mala"),
    "card": ("connect card", "porta-cartão", "porta cartão", "cartão magsafe"),
    "perfume": ("perfume", "body mist", "frasco", "body splash"),
    "cosmetico": ("sérum", "serum", "creme", "shampoo", "condicionador", "máscara", "gelatina", "óleo", "hidratante",
                  "biotina", "cápsula", "whey", "pote", "bisnaga", "batom", "base "),
}


# estampas de catálogo: se o pedido tem a estampa e a foto da capinha não a mostra, o produto não confere
ESTAMPAS = {"xadrez vichy": ("xadrez", "vichy")}


def _categorias(texto: str) -> set[str]:
    t = (texto or "").lower()
    return {cat for cat, termos in CATEGORIAS.items() if any(x in t for x in termos)}


class PericiaIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    anexo: dict = {}


def pericia(order_id: Optional[str], customer_id: Optional[str], a: dict) -> dict:
    desc = (a.get("descricao_visual") or "").strip()
    dl = desc.lower()
    p = _pedido(order_id) if order_id else None
    itens = _q("SELECT descricao, texto_personalizacao FROM itens_pedido WHERE order_id=?", (order_id,)) if p else []
    sinais: list[dict] = []
    score = 100

    def sinal(nome: str, peso: int, detalhe: str) -> None:
        nonlocal score
        score += peso
        sinais.append({"sinal": nome, "peso": peso, "detalhe": detalhe})

    # 1) reuso da imagem (hash perceptual): só anexos enviados ANTES deste (nunca o próprio)
    reuso = []
    h = a.get("hash_perceptual")
    if h:
        reuso = _q("""SELECT anexo_id, conversation_id, order_id, customer_id, canal, enviado_em, tipo,
                             exif_data_captura, exif_modelo_camera, exif_software, descricao_visual
                      FROM anexos_conversas WHERE hash_perceptual=? AND anexo_id IS NOT ?
                      AND (order_id IS NOT ? OR customer_id IS NOT ?)
                      AND (? IS NULL OR enviado_em < ?) ORDER BY enviado_em""",
                   (h, a.get("anexo_id"), order_id, customer_id, a.get("enviado_em"), a.get("enviado_em")))
        if reuso:
            outros = len({r["customer_id"] for r in reuso if r["customer_id"] != customer_id})
            sinal("foto_reaproveitada", -55, f"mesma imagem (hash {h}) já enviada em {len(reuso)} anexo(s) de outro(s) "
                  f"pedido(s), {outros} outro(s) cliente(s): " + ", ".join(f"{r['anexo_id']}/{r['order_id']}" for r in reuso))

    # 2) EXIF
    cap = _parse(a.get("exif_data_captura"))
    software = (a.get("exif_software") or "").strip()
    modelo = (a.get("exif_modelo_camera") or "").strip() or None
    presente = bool(cap or modelo or software)
    entrega = _parse(p.get("data_entrega")) if p else None
    data_ped = _parse(p.get("data_pedido")) if p else None
    antes_entrega = bool(cap and ((entrega and cap.date() < entrega.date()) or (data_ped and cap.date() < data_ped.date())))
    antes_pedido = bool(cap and data_ped and cap.date() < data_ped.date())
    sw_low = f" {software.lower()} "
    software_suspeito = bool(software) and any(x in sw_low for x in SOFTWARE_SUSPEITO)
    if not presente:
        sinal("exif_ausente", -15, "arquivo sem metadados (o WhatsApp costuma remover); não prova nada sozinho")
    if antes_entrega:
        ref = "do pedido" if antes_pedido else "da entrega"
        sinal("foto_antes_da_entrega", -30, f"EXIF de {cap:%d/%m/%Y %H:%M}, antes {ref} "
              f"({(data_ped if antes_pedido else entrega):%d/%m/%Y})")
    elif cap and entrega:
        sinal("foto_depois_da_entrega", 0, f"EXIF de {cap:%d/%m/%Y %H:%M}, depois da entrega ({entrega:%d/%m/%Y})")
    if software_suspeito:
        sinal("software_de_edicao", -25, f"arquivo passou por editor/gerador: {software}")

    # 3) indícios de IA / edição
    indicios = []
    if software_suspeito:
        indicios.append(f"metadado de software de edição/IA: {software}")
    for termo, texto in INDICIOS_DESCRICAO.items():
        if termo in dl and texto not in indicios:
            indicios.append(texto)
    n_desc = len(indicios) - (1 if software_suspeito else 0)
    if n_desc:
        sinal("indicios_visuais_de_edicao", -min(15, 5 * n_desc), "; ".join(indicios[1 if software_suspeito else 0:]))

    # 4) avaria visível?
    tem_termo = any(t in dl for t in AVARIA_TERMOS)
    ambiguo = any(t in dl for t in AMBIGUO_TERMOS)
    avaria = tem_termo
    confianca = 0.0 if not desc else (0.9 if tem_termo and not ambiguo else 0.5 if tem_termo else 0.2)
    if not desc:
        sinal("sem_descricao", -20, "anexo sem descrição visual: não há como confirmar a avaria")
    elif not tem_termo:
        sinal("avaria_nao_visivel", -30, "a imagem não mostra dano")
    elif ambiguo:
        sinal("imagem_inconclusiva", -20, "a imagem não mostra o dano com clareza")
    if a.get("tipo") == "foto_embalagem":
        sinal("so_embalagem", -5, "foto só da embalagem, não do produto")

    # 5) produto confere com o pedido?
    produto_confere = True
    motivo_produto = None
    if p:
        cat_ped = _categorias(" ".join([p.get("descricao_produto") or ""] + [i["descricao"] or "" for i in itens]))
        cat_foto = _categorias(desc)
        if cat_foto and cat_ped and not (cat_foto & cat_ped):
            produto_confere = False
            motivo_produto = f"foto mostra {', '.join(sorted(cat_foto))}; pedido é {p.get('descricao_produto')}"
        desc_ped = (p.get("descricao_produto") or "").lower()
        for estampa, termos in ESTAMPAS.items():
            if estampa in desc_ped and cat_foto & {"case"} and not any(t in dl for t in termos):
                produto_confere = False
                motivo_produto = (f"pedido é estampa {estampa.title()} ({p.get('descricao_produto')}); "
                                  f"a foto mostra: {desc.rstrip('.')}")
        textos = [i["texto_personalizacao"] for i in itens if i.get("texto_personalizacao")]
        if re.search(r"não aparece[m]? (a |o |as |os )?(personaliza|nome|inicia|foto)", dl):
            produto_confere = False
            motivo_produto = f"personalização do pedido ({', '.join(textos) or p.get('personalizacao')}) não aparece na foto"
    if not produto_confere:
        sinal("produto_nao_confere", -20, motivo_produto or "produto não confere")

    # 6) prazo de 7 dias para avaria
    enviado = _parse(a.get("enviado_em")) or NOW_ENV
    if entrega and (enviado.date() - entrega.date()).days > 7:
        sinal("fora_do_prazo_avaria", 0, f"reclamação {(enviado.date() - entrega.date()).days} dias após a entrega "
              "(política: até 7 dias)")

    score = max(0, min(100, score))
    return {
        "anexo_id": a.get("anexo_id"), "order_id": order_id, "customer_id": customer_id,
        "avaria_detectada": avaria, "confianca": round(confianca, 2),
        "reuso": [{k: r[k] for k in ("anexo_id", "order_id", "customer_id", "enviado_em", "descricao_visual",
                                      "exif_data_captura", "exif_modelo_camera", "canal")} for r in reuso],
        "exif": {"presente": presente, "data_captura": a.get("exif_data_captura") or None,
                 "antes_da_entrega": antes_entrega, "antes_do_pedido": antes_pedido,
                 "software": software or None, "software_suspeito": software_suspeito, "modelo": modelo,
                 "data_entrega": p.get("data_entrega") if p else None},
        "indicios_ia": indicios, "produto_confere": produto_confere, "motivo_produto": motivo_produto,
        "descricao_visual": desc, "hash_perceptual": h,
        "score_veracidade": score, "sinais": sinais,
    }


@erp.post("/pericia-foto")
def post_pericia(body: PericiaIn, request: Request):
    if body.order_id and not _pedido(body.order_id):
        return _erro(404, "pedido_nao_encontrado", f"Pedido {body.order_id} não existe no ERP")
    res = pericia(body.order_id, body.customer_id, body.anexo or {})
    _log(request, "erp", "pericia_foto", body.model_dump(), 200,
         {k: res[k] for k in ("avaria_detectada", "confianca", "score_veracidade")} | {"reuso": len(res["reuso"])},
         _case_id(request, body.model_dump()))
    return res


# ------------------------------------------------------------------ chamados internos

chamados = APIRouter(prefix="/api/chamados", tags=["Chamados"])


def _chamado(cid: str) -> Optional[dict]:
    c = _q1("SELECT * FROM sim_chamados WHERE chamado_id=?", (cid,))
    if c:
        c["historico"] = json.loads(c["historico"] or "[]")
    return c


@chamados.post("")
def post_chamado(request: Request, body: dict = Body(...)):
    key, anterior = _idem_anterior(request)
    if anterior is not None:
        return anterior
    cid = _novo_id("CHM")
    agora = _iso(_agora())
    hist = [{"ts": agora, "status": "aberto", "nota": body.get("assunto") or "", "por": request.headers.get("X-Agente")}]
    _insert("sim_chamados", {"chamado_id": cid, "conversation_id": body.get("conversation_id"),
                             "customer_id": body.get("customer_id"), "order_id": body.get("order_id"),
                             "canal": body.get("canal"), "assunto": body.get("assunto"), "status": "aberto",
                             "criado_em": agora, "atualizado_em": agora, "historico": hist})
    res = {"chamado_id": cid, "status": "aberto", "criado_em": agora}
    _log(request, "chamados", "abrir_chamado", body, 200, res, _case_id(request, body), key)
    return res


@chamados.patch("/{chamado_id}")
def patch_chamado(chamado_id: str, request: Request, body: dict = Body(...)):
    c = _chamado(chamado_id)
    if not c:
        return _erro(404, "chamado_nao_encontrado", f"Chamado {chamado_id} não existe")
    agora = _iso(_agora())
    status = body.get("status") or c["status"]
    c["historico"].append({"ts": agora, "status": status, "nota": body.get("nota") or "",
                           "por": request.headers.get("X-Agente")})
    _exec("UPDATE sim_chamados SET status=?, atualizado_em=?, historico=? WHERE chamado_id=?",
          (status, agora, json.dumps(c["historico"], ensure_ascii=False), chamado_id))
    c = _chamado(chamado_id)
    _log(request, "chamados", "atualizar_chamado", {"chamado_id": chamado_id, **body}, 200,
         {"chamado_id": chamado_id, "status": status}, _case_id(request, body))
    return c


@chamados.get("/{chamado_id}")
def get_chamado(chamado_id: str):
    c = _chamado(chamado_id)
    return c or _erro(404, "chamado_nao_encontrado", f"Chamado {chamado_id} não existe")


# ------------------------------------------------------------------ logística

logistica = APIRouter(prefix="/api/logistica", tags=["Logística"])


@logistica.post("/trocas")
def post_troca(request: Request, body: dict = Body(...)):
    key, anterior = _idem_anterior(request)
    if anterior is not None:
        return anterior
    order_id = body.get("order_id")
    p = _pedido(order_id or "")
    if not p:
        return _erro(404, "pedido_nao_encontrado", f"Pedido {order_id} não existe no ERP")
    tipo = body.get("tipo") or "reenvio"
    if tipo not in ("reenvio", "troca", "nova_producao"):
        return _erro(422, "tipo_invalido", "tipo deve ser reenvio, troca ou nova_producao")
    sku = body.get("sku") or p["sku"]
    qtd = int(body.get("quantidade") or 1)
    prod = _q1("SELECT custo_unitario FROM produtos WHERE sku=?", (sku,)) or {"custo_unitario": 0}
    frete = _q1("SELECT AVG(custo_frete) f, AVG(custo_manuseio) m FROM trocas_reenvios WHERE transportadora=?",
                (p["transportadora"],)) or {}
    custo = round((prod["custo_unitario"] or 0) * qtd + (frete.get("f") or 20.0) + (frete.get("m") or 4.5), 2)
    n_rep = (_q1("SELECT COUNT(*) n FROM trocas_reenvios WHERE order_id_original=?", (order_id,))["n"]
             + _q1("SELECT COUNT(*) n FROM sim_trocas WHERE order_id=?", (order_id,))["n"])
    sol = _novo_id("OMS")
    rep = f"{order_id}-R{n_rep + 1}"
    agora = _iso(_agora())
    _insert("sim_trocas", {"solicitacao_id": sol, "order_id": order_id, "sku": sku, "tipo": tipo,
                           "motivo": body.get("motivo"), "quantidade": qtd, "pedido_reposicao_id": rep,
                           "custo_total": custo, "status": "solicitado", "criado_em": agora,
                           "solicitado_por": request.headers.get("X-Agente"), "idempotency_key": key})
    res = {"solicitacao_id": sol, "pedido_reposicao_id": rep, "custo_total": custo, "status": "solicitado",
           "tipo": tipo, "sku": sku, "order_id": order_id, "cd_expedicao": p.get("cd_separacao") or "CD1",
           "transportadora": p["transportadora"]}
    _log(request, "logistica", f"criar_{tipo}", body, 200, res, _case_id(request, body), key)
    return res


@logistica.post("/trocas/{solicitacao_id}/cancelar")
def cancelar_troca(solicitacao_id: str, request: Request, body: dict = Body(default={})):
    """Desfazer (nível B): cancela a reposição. Idempotente."""
    t = _q1("SELECT * FROM sim_trocas WHERE solicitacao_id=?", (solicitacao_id,))
    if not t:
        return _erro(404, "troca_nao_encontrada", f"Solicitação {solicitacao_id} não existe")
    if t["status"] == "cancelado":
        return {"solicitacao_id": solicitacao_id, "pedido_reposicao_id": t["pedido_reposicao_id"], "status": "cancelado",
                "idempotente": True}
    _exec("UPDATE sim_trocas SET status='cancelado' WHERE solicitacao_id=?", (solicitacao_id,))
    res = {"solicitacao_id": solicitacao_id, "pedido_reposicao_id": t["pedido_reposicao_id"], "status": "cancelado",
           "cancelado_em": _iso(_agora()), "motivo": (body or {}).get("motivo"), "idempotente": False}
    _log(request, "logistica", "cancelar_troca", {"solicitacao_id": solicitacao_id, **(body or {})}, 200, res,
         _case_id(request, body))
    return res


@logistica.post("/chamado-transportadora")
def post_chamado_transportadora(request: Request, body: dict = Body(...)):
    key, anterior = _idem_anterior(request)
    if anterior is not None:
        return anterior
    order_id = body.get("order_id")
    p = _pedido(order_id or "")
    if not p:
        return _erro(404, "pedido_nao_encontrado", f"Pedido {order_id} não existe no ERP")
    t = _q1("SELECT * FROM transportadoras WHERE transportadora=?", (p["transportadora"],)) or {}
    horas = int(t.get("prazo_resposta_chamado_horas") or 48)
    sigla = {"Expressa Já": "EXJ", "TransNorte": "TRN", "Rápido Sul": "RPS", "ViaLog": "VLG",
             "Correio Ágil": "CAG"}.get(p["transportadora"], "TRP")
    agora = _agora()
    cid = _novo_id(sigla)
    protocolo = f"{sigla}{agora:%Y%m%d}{secrets.randbelow(10**5):05d}"
    prazo = _iso(agora + timedelta(hours=horas))
    _insert("sim_chamados_transportadora", {
        "chamado_id": cid, "protocolo": protocolo, "order_id": order_id, "transportadora": p["transportadora"],
        "codigo_rastreio": p["codigo_rastreio"], "motivo": body.get("motivo"), "prazo_resposta": prazo,
        "status": "aberto", "criado_em": _iso(agora), "aberto_por": request.headers.get("X-Agente"),
        "idempotency_key": key})
    res = {"chamado_id": cid, "protocolo": protocolo, "transportadora": p["transportadora"],
           "codigo_rastreio": p["codigo_rastreio"], "prazo_resposta": prazo, "prazo_resposta_horas": horas,
           "status": "aberto", "contato": t.get("email_contato")}
    _log(request, "logistica", "abrir_chamado_transportadora", body, 200, res, _case_id(request, body), key)
    return res


# ------------------------------------------------------------------ financeiro

financeiro = APIRouter(prefix="/api/financeiro", tags=["Financeiro"])


@financeiro.post("/reembolsos")
def post_reembolso(request: Request, body: dict = Body(...)):
    key = request.headers.get("Idempotency-Key")
    case_id = _case_id(request, body)
    if key:
        prev = _q1("SELECT resposta FROM sim_reembolsos WHERE idempotency_key=?", (key,))
        if prev:
            res = json.loads(prev["resposta"]) | {"idempotente": True}
            _log(request, "financeiro", "reembolso_repetido", body, 200, res, case_id, key)
            return res
    order_id = body.get("order_id")
    try:
        valor = round(float(body.get("valor")), 2)
    except (TypeError, ValueError):
        return _erro(422, "valor_invalido", "valor deve ser numérico")
    p = _pedido(order_id or "")
    if not p:
        return _erro(404, "pedido_nao_encontrado", f"Pedido {order_id} não existe no ERP")
    if valor <= 0:
        return _erro(422, "valor_invalido", "valor deve ser positivo")
    ja = _q1("SELECT COALESCE(SUM(valor),0) s FROM sim_reembolsos WHERE order_id=? AND status='aprovado'",
             (order_id,))["s"]
    ja += _q1("SELECT COALESCE(SUM(valor),0) s FROM estornos WHERE order_id=? AND status='concluido'", (order_id,))["s"]
    if valor + ja > (p["valor"] or 0) + 0.01:
        r = _erro(422, "valor_acima_do_pedido", f"Reembolso de R$ {valor:.2f} + já reembolsado R$ {ja:.2f} passa o "
                  f"valor pago no pedido (R$ {p['valor']:.2f})")
        _log(request, "financeiro", "reembolso_recusado", body, 422, json.loads(r.body), case_id, key)
        return r
    aprov_id = body.get("aprovacao_id")
    if valor > LIMITE_REEMBOLSO_SEM_APROVACAO:
        ap = _q1("SELECT * FROM sim_aprovacoes WHERE aprovacao_id=?", (aprov_id,)) if aprov_id else None
        limite = None
        if ap and ap["status"] in ("aprovada", "ajustada"):
            aj = json.loads(ap["ajuste"] or "{}") or {}
            limite = float(aj.get("valor") or ap["valor"] or 0)
        if limite is None or valor > limite + 0.01:
            motivo = ("sem aprovação" if not ap else f"aprovação {aprov_id} está '{ap['status']}'"
                      if limite is None else f"aprovação {aprov_id} cobre até R$ {limite:.2f}")
            r = _erro(403, "exige_aprovacao", f"Reembolso de R$ {valor:.2f} acima de R$ "
                      f"{LIMITE_REEMBOLSO_SEM_APROVACAO:.0f} exige aprovação humana aprovada ({motivo})",
                      limite_sem_aprovacao=LIMITE_REEMBOLSO_SEM_APROVACAO)
            _log(request, "financeiro", "reembolso_bloqueado", body, 403, json.loads(r.body), case_id, key)
            return r
    rid = _novo_id("REB")
    agora = _iso(_agora())
    pg = _q1("SELECT metodo FROM pagamentos_gateway WHERE order_id=? AND status IN ('capturada','paga') "
             "ORDER BY data_hora_criacao DESC", (order_id,)) or {}
    prazo = "até 2 faturas" if (pg.get("metodo") or "").startswith("cartao") else "até 5 dias úteis"
    res = {"reembolso_id": rid, "order_id": order_id, "valor": valor, "status": "aprovado", "metodo": pg.get("metodo"),
           "prazo_credito": prazo, "aprovacao_id": aprov_id, "criado_em": agora, "idempotente": False}
    with _write_lock:
        con = _con()
        try:
            con.execute("INSERT INTO sim_reembolsos (reembolso_id, order_id, valor, motivo, aprovacao_id, status, "
                        "criado_em, solicitado_por, idempotency_key, resposta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rid, order_id, valor, body.get("motivo"), aprov_id, "aprovado", agora,
                         request.headers.get("X-Agente"), key, json.dumps(res, ensure_ascii=False)))
            con.commit()
        except sqlite3.IntegrityError:          # corrida com a mesma chave: devolve o que foi gravado
            prev = con.execute("SELECT resposta FROM sim_reembolsos WHERE idempotency_key=?", (key,)).fetchone()
            return json.loads(prev["resposta"]) | {"idempotente": True}
        finally:
            con.close()
    _log(request, "financeiro", "reembolso", body, 200, res, case_id, key)
    return res


@financeiro.post("/reembolsos/{reembolso_id}/estornar")
def estornar_reembolso(reembolso_id: str, request: Request, body: dict = Body(default={})):
    """Desfazer (nível B): estorna o reembolso (o valor volta a contar como não reembolsado). Idempotente."""
    r = _q1("SELECT * FROM sim_reembolsos WHERE reembolso_id=?", (reembolso_id,))
    if not r:
        return _erro(404, "reembolso_nao_encontrado", f"Reembolso {reembolso_id} não existe")
    if r["status"] == "estornado":
        return {"reembolso_id": reembolso_id, "valor": r["valor"], "status": "estornado", "idempotente": True}
    _exec("UPDATE sim_reembolsos SET status='estornado' WHERE reembolso_id=?", (reembolso_id,))
    res = {"reembolso_id": reembolso_id, "order_id": r["order_id"], "valor": r["valor"], "status": "estornado",
           "estornado_em": _iso(_agora()), "motivo": (body or {}).get("motivo"), "idempotente": False}
    _log(request, "financeiro", "estornar_reembolso", {"reembolso_id": reembolso_id, **(body or {})}, 200, res,
         _case_id(request, body))
    return res


# ------------------------------------------------------------------ aprovações

aprovacoes = APIRouter(prefix="/api/aprovacoes", tags=["Aprovações"])
_JSON_COLS = ("opcoes", "evidencias", "ajuste", "recomendacao")
_colunas_ok: set[str] = set()
_colunas_lock = threading.Lock()


def _garantir_colunas() -> None:
    """recomendacao/concordou chegaram depois do build_db: acrescenta em bancos já construídos."""
    if str(DB_PATH) in _colunas_ok:
        return
    with _colunas_lock:          # lock próprio: quem chama pode já estar segurando _write_lock
        con = _con()
        try:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(sim_aprovacoes)")}
            for col, tipo in (("recomendacao", "TEXT"), ("concordou", "INTEGER"), ("aprovador_id", "TEXT"),
                              ("aprovador_cargo", "TEXT")):
                if col not in cols:
                    con.execute(f"ALTER TABLE sim_aprovacoes ADD COLUMN {col} {tipo}")
            con.commit()
        finally:
            con.close()
    _colunas_ok.add(str(DB_PATH))


# Alçada por papel (politicas.csv, limites_por_papel). Supervisor aprova acima dos limites.
_CHAVE_ALCADA = {"reembolso": "reembolso_max_rs", "reenvio": "reenvio_ou_reimpressao_max_rs",
                 "nova_producao": "reenvio_ou_reimpressao_max_rs", "troca": "troca_max_rs"}
_SO_SUPERVISOR = {"cosmetovigilancia", "registrar_cosmetovigilancia"}


def _pessoa(aprovador: Optional[str], aprovador_id: Optional[str]) -> Optional[dict]:
    for chave in (aprovador_id, aprovador):
        if not chave:
            continue
        p = _q1("SELECT colaborador_id, nome, cargo FROM equipe_atendimento WHERE status='ativo' AND "
                "(colaborador_id=? OR lower(nome)=lower(?))", (chave, chave))
        if p:
            return p
    return None


def _alcada(cargo: str, acao: Optional[str]) -> Optional[float]:
    """Limite em R$ que o cargo pode aprovar para a ação; None = sem limite (supervisor)."""
    if cargo == "supervisor":
        return None
    if acao in _SO_SUPERVISOR:
        return -1.0
    chave = _CHAVE_ALCADA.get(acao or "")
    if not chave:
        return 0.0
    papel = "atendente"  # o analista não tem alçada própria em politicas.csv: vale a do atendente
    r = _q1("SELECT valor FROM politicas WHERE secao='limites_por_papel' AND regra=?", (f"{papel}.{chave}",))
    try:
        return float(r["valor"]) if r else 0.0
    except (TypeError, ValueError):
        return 0.0


def _responsavel_do_caso(case_id: Optional[str]) -> Optional[dict]:
    try:
        from core.state import store
        c = store.get(case_id) if case_id else None
        return c.responsavel if c else None
    except Exception:
        return None


def _brl(v) -> str:
    return f"R$ {float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _aprovacao(aid: str) -> Optional[dict]:
    _garantir_colunas()
    a = _q1("SELECT * FROM sim_aprovacoes WHERE aprovacao_id=?", (aid,))
    if a:
        for c in _JSON_COLS:
            a[c] = json.loads(a[c]) if a.get(c) else None
        if a.get("concordou") is not None:
            a["concordou"] = bool(a["concordou"])
    return a


@aprovacoes.post("")
async def post_aprovacao(request: Request, body: dict = Body(...)):
    key, anterior = _idem_anterior(request)
    if anterior is not None:
        return anterior
    agora = _agora()
    prazo_s = int(body.get("prazo_s") or config.APPROVAL_SLA_S)
    aid = _novo_id("APR")
    row = {"aprovacao_id": aid, "case_id": body.get("case_id"), "titulo": body.get("titulo") or "Aprovação",
           "resumo": body.get("resumo") or "", "nivel": body.get("nivel") or "C", "valor": body.get("valor"),
           "acao_proposta": body.get("acao_proposta"), "opcoes": body.get("opcoes") or ["aprovar", "negar", "ajustar"],
           "evidencias": body.get("evidencias") or [], "prazo_s": prazo_s, "status": "pendente",
           "criado_em": _iso(agora), "expira_em": _iso(agora + timedelta(seconds=prazo_s)),
           "solicitante": request.headers.get("X-Agente")}
    if body.get("solicitante"):
        row["solicitante"] = body["solicitante"]
    _garantir_colunas()
    if body.get("recomendacao"):
        row["recomendacao"] = body["recomendacao"]
    _insert("sim_aprovacoes", row)
    res = {"aprovacao_id": aid, "status": "pendente", "expira_em": row["expira_em"], "criado_em": row["criado_em"],
           "solicitante": row["solicitante"]}
    _log(request, "aprovacoes", "pedir_aprovacao", body, 200, res, body.get("case_id"), key)
    await bus.publish("approval.requested", {
        "aprovacao_id": aid, "titulo": row["titulo"], "resumo": row["resumo"], "nivel": row["nivel"],
        "valor": row["valor"], "acao_proposta": row["acao_proposta"], "opcoes": row["opcoes"],
        "evidencias": row["evidencias"], "expira_em": row["expira_em"], "recomendacao": body.get("recomendacao"),
        "solicitante": row["solicitante"]},
        case_id=row["case_id"], agent=request.headers.get("X-Agente") or "mensageiro")
    return res


@aprovacoes.get("")
def list_aprovacoes(status: Optional[str] = None, case_id: Optional[str] = None):
    sql, params = "SELECT aprovacao_id FROM sim_aprovacoes WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if case_id:
        sql += " AND case_id=?"
        params.append(case_id)
    return [_aprovacao(r["aprovacao_id"]) for r in _q(sql + " ORDER BY criado_em DESC", params)]


@aprovacoes.get("/{aprovacao_id}")
def get_aprovacao(aprovacao_id: str):
    return _aprovacao(aprovacao_id) or _erro(404, "aprovacao_nao_encontrada", f"Aprovação {aprovacao_id} não existe")


_DECISAO_STATUS = {"aprovar": "aprovada", "negar": "negada", "ajustar": "ajustada"}


@aprovacoes.post("/{aprovacao_id}/decisao")
async def post_decisao(aprovacao_id: str, request: Request, body: dict = Body(...)):
    decisao = body.get("decisao")
    if decisao not in _DECISAO_STATUS:
        return _erro(422, "decisao_invalida", "decisao deve ser aprovar, negar ou ajustar")
    with _write_lock:
        a = _aprovacao(aprovacao_id)
        if not a:
            return _erro(404, "aprovacao_nao_encontrada", f"Aprovação {aprovacao_id} não existe")
        if a["status"] != "pendente":
            return _erro(409, "ja_decidida", f"Aprovação já decidida: {a['decisao']} por {a['aprovador']} "
                         f"via {a['canal']}", aprovacao=a)
        agora = _agora()
        tempo = round((agora - datetime.fromisoformat(a["criado_em"])).total_seconds(), 1)
        ajuste = body.get("ajuste") or None
        # --- segregação de funções e alçada (politicas.csv / equipe_atendimento)
        aprovador = body.get("aprovador") or "supervisor"
        pessoa = _pessoa(aprovador, body.get("aprovador_id"))
        ident = {x for x in (aprovador, body.get("aprovador_id"), (pessoa or {}).get("colaborador_id")) if x}
        if a.get("solicitante") and a["solicitante"] in ident:
            return _erro(403, "segregacao", "Você pediu essa ação; outra pessoa precisa aprovar.")
        resp = _responsavel_do_caso(a.get("case_id"))
        if resp and resp.get("colaborador_id") in ident:
            return _erro(403, "segregacao", "Este caso está com você; outra pessoa precisa aprovar.")
        if pessoa and decisao in ("aprovar", "ajustar"):
            acao = (ajuste or {}).get("acao") or a.get("acao_proposta")
            valor = float((ajuste or {}).get("valor") if (ajuste or {}).get("valor") is not None else (a.get("valor") or 0))
            lim = _alcada(pessoa["cargo"], acao)
            if lim is not None and (lim < 0 or valor > lim + 0.01):
                txt = ("Só um supervisor pode aprovar esta ação." if lim < 0 else
                       f"{(acao or 'Ação').replace('_', ' ').capitalize()} de {_brl(valor)} passa da sua alçada "
                       f"({pessoa['cargo']}: até {_brl(lim)}); peça a um supervisor.")
                return _erro(403, "alcada", txt, cargo=pessoa["cargo"], limite=lim)
        rec = a.get("recomendacao") or {}
        concordou = None
        if rec.get("decisao_sugerida"):
            concordou = rec["decisao_sugerida"] == decisao      # ajustar = divergência parcial
        _garantir_colunas()
        con = _con()
        try:
            con.execute("UPDATE sim_aprovacoes SET status=?, decisao=?, aprovador=?, canal=?, ajuste=?, decidido_em=?, "
                        "tempo_resposta_s=?, concordou=?, aprovador_id=?, aprovador_cargo=? "
                        "WHERE aprovacao_id=? AND status='pendente'",
                        (_DECISAO_STATUS[decisao], decisao, aprovador,
                         body.get("canal") or "painel", json.dumps(ajuste, ensure_ascii=False) if ajuste else None,
                         _iso(agora), tempo, None if concordou is None else int(concordou),
                         (pessoa or {}).get("colaborador_id"), (pessoa or {}).get("cargo"), aprovacao_id))
            con.commit()
        finally:
            con.close()
    a = _aprovacao(aprovacao_id)
    _log(request, "aprovacoes", "decidir_aprovacao", {"aprovacao_id": aprovacao_id, **body}, 200,
         {"status": a["status"], "tempo_resposta_s": tempo}, a["case_id"])
    await bus.publish("approval.decided", {
        "aprovacao_id": aprovacao_id, "decisao": decisao, "aprovador": a["aprovador"], "canal": a["canal"],
        "ajuste": ajuste, "tempo_resposta_s": tempo, "concordou": concordou, "recomendacao": a.get("recomendacao"),
        "aprovador_id": a.get("aprovador_id"), "aprovador_cargo": a.get("aprovador_cargo")},
        case_id=a["case_id"], agent="humano")
    return a


# ------------------------------------------------------------------ mensageria e log

mensagens = APIRouter(prefix="/api/mensagens", tags=["Mensageria"])


@mensagens.post("")
async def post_mensagem(request: Request, body: dict = Body(...)):
    key, anterior = _idem_anterior(request)
    if anterior is not None:
        return anterior
    if not (body.get("texto") or "").strip():
        return _erro(422, "texto_vazio", "texto é obrigatório")
    mid = _novo_id("MSG")
    agora = _iso(_agora())
    canal = body.get("canal") or "whatsapp"
    _insert("sim_mensagens", {"message_id": mid, "canal": canal, "customer_id": body.get("customer_id"),
                              "conversation_id": body.get("conversation_id"), "case_id": body.get("case_id"),
                              "texto": body["texto"], "enviado_em": agora,
                              "telegram_chat_id": body.get("telegram_chat_id"),
                              "autor": request.headers.get("X-Agente") or "goxys"})
    res = {"message_id": mid, "enviado_em": agora, "canal": canal}
    _log(request, "mensageria", "enviar_mensagem", body, 200, res, _case_id(request, body), key)
    data = {"canal": canal, "texto": body["texto"], "message_id": mid, "customer_id": body.get("customer_id"),
            "conversation_id": body.get("conversation_id")}
    if body.get("telegram_chat_id"):
        data["telegram_chat_id"] = body["telegram_chat_id"]
    await bus.publish("message.sent", data, case_id=body.get("case_id"), agent=request.headers.get("X-Agente") or "voz")
    return res


@mensagens.get("")
def list_mensagens(conversation_id: Optional[str] = None, case_id: Optional[str] = None):
    sql, params = "SELECT * FROM sim_mensagens WHERE 1=1", []
    if conversation_id:
        sql += " AND conversation_id=?"
        params.append(conversation_id)
    if case_id:
        sql += " AND case_id=?"
        params.append(case_id)
    return _q(sql + " ORDER BY enviado_em", params)


eventos = APIRouter(prefix="/api/eventos", tags=["Log"])


@eventos.get("")
def list_eventos(case_id: Optional[str] = None, limite: int = Query(500, le=5000)):
    sql, params = "SELECT * FROM sim_eventos", []
    if case_id:
        sql += " WHERE case_id=?"
        params.append(case_id)
    rows = _q(sql + " ORDER BY evento_id DESC LIMIT ?", params + [limite])
    for r in rows:
        for c in ("parametros", "resultado"):
            try:
                r[c] = json.loads(r[c]) if r[c] else None
            except ValueError:
                pass
    return list(reversed(rows))


# ------------------------------------------------------------------ portal TransNorte (sem API)

portal = APIRouter(prefix="/portal", tags=["Portal TransNorte"], include_in_schema=False)
_sessoes: dict[str, str] = {}
COOKIE = "TNSESSIONID"

_CSS = """
body{margin:0;background:#d9d9d9 url('data:image/gif;base64,R0lGODlhBAAEAIAAAMzMzP///yH5BAAAAAAALAAAAAAEAAQAAAIGTACXaHkFADs=');
 font-family:Verdana,Tahoma,Arial,sans-serif;font-size:11px;color:#222}
#wrap{width:780px;max-width:100%;margin:0 auto;background:#fff;border:1px solid #7a7a7a;border-top:0}
#topo{background:linear-gradient(#0b3d91,#06265c);color:#fff;padding:10px 14px;border-bottom:4px solid #f2b600}
#topo b{font-family:'Arial Black',Arial,sans-serif;font-size:22px;letter-spacing:-1px}
#topo b span{color:#f2b600}#topo small{display:block;font-size:10px;color:#c9d6ef}
#menu{background:#e8e8e8;border-bottom:1px solid #aaa;padding:4px 10px}
#menu a{color:#06265c;margin-right:14px;font-weight:bold;text-decoration:none}#menu a:hover{text-decoration:underline}
#conteudo{padding:14px}
h2{font-size:13px;color:#06265c;border-bottom:1px dotted #06265c;margin:4px 0 10px;padding-bottom:3px}
fieldset{border:1px solid #9aa;padding:10px;margin-bottom:12px}legend{font-weight:bold;color:#06265c}
input[type=text],input[type=password]{font-size:11px;border:1px solid #7f9db9;padding:2px;width:180px}
input[type=submit]{font-size:11px;background:#ece9d8;border:1px solid #003c74;padding:2px 10px;cursor:pointer}
table.grid{border-collapse:collapse;width:100%;margin-top:6px}
table.grid th{background:#06265c;color:#fff;text-align:left;padding:4px;font-size:10px;text-transform:uppercase}
table.grid td{border:1px solid #c5c5c5;padding:4px}table.grid tr:nth-child(even) td{background:#f3f6fb}
.aviso{background:#fffbe0;border:1px solid #e6c200;padding:6px;margin:8px 0}
.erro{background:#ffe5e5;border:1px solid #c00;color:#900;padding:6px;margin:8px 0}
.dado{font-weight:bold;color:#06265c}
#rodape{font-size:9px;color:#666;text-align:center;padding:8px;border-top:1px solid #ccc;background:#f4f4f4}
"""

_STATUS_TXT = {"coletado": "OBJETO COLETADO NO REMETENTE", "em_transferencia": "EM TRANSFERÊNCIA ENTRE UNIDADES",
               "chegada_unidade_destino": "CHEGOU NA UNIDADE DE DESTINO", "saiu_para_entrega": "SAIU PARA ENTREGA",
               "entregue": "ENTREGUE AO DESTINATÁRIO", "extraviado": "OBJETO EXTRAVIADO"}


def _pagina(titulo: str, corpo: str, logado: bool = False) -> HTMLResponse:
    menu = ('<a href="/portal">Rastreamento</a><a href="/portal">Ocorrências</a><a href="/portal">2ª via CT-e</a>'
            '<a href="/portal/sair">Sair</a>') if logado else '<a href="/portal">Área do cliente</a>'
    return HTMLResponse(f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(titulo)} - TransNorte Cargas</title>
<style>{_CSS}</style></head><body><div id="wrap">
<div id="topo"><b>TRANS<span>NORTE</span></b><small>Cargas e Encomendas S.A. &middot; Portal do Cliente Corporativo &middot; v2.3.1</small></div>
<div id="menu">{menu}</div><div id="conteudo">{corpo}</div>
<div id="rodape">TransNorte Cargas e Encomendas S.A. - CNPJ 37.055.102/0001-14 - Melhor visualizado em 1024x768 com Internet Explorer 8 ou superior.<br>
Central de Atendimento: (41) 97482-5016 - Seg a Sex das 8h às 18h</div></div></body></html>""")


def _logado(request: Request) -> Optional[str]:
    return _sessoes.get(request.cookies.get(COOKIE) or "")


def _form_login(erro: str = "", nxt: str = "") -> str:
    msg = f'<div class="erro">{html.escape(erro)}</div>' if erro else ""
    return f"""<h2>Acesso restrito - Clientes corporativos</h2>{msg}
<form method="post" action="/portal/login"><fieldset><legend>Identificação</legend>
<input type="hidden" name="next" value="{html.escape(nxt)}">
<table><tr><td>Usuário:</td><td><input type="text" name="usuario" id="usuario"></td></tr>
<tr><td>Senha:</td><td><input type="password" name="senha" id="senha"></td></tr>
<tr><td></td><td><input type="submit" value="Entrar"></td></tr></table></fieldset></form>
<div class="aviso">Atenção: a TransNorte <b>não disponibiliza API de rastreamento</b>. Consultas somente por este portal.</div>"""


@portal.get("", response_class=HTMLResponse)
def portal_home(request: Request, next: str = ""):
    user = _logado(request)
    if not user:
        return _pagina("Login", _form_login(nxt=next))
    return _pagina("Rastreamento", f"""<h2>Rastreamento de encomendas</h2>
<p>Bem-vindo, <span class="dado">{html.escape(user)}</span>. Informe o código de rastreio ou o número do pedido do embarcador.</p>
<form method="get" action="/portal/rastreio"><fieldset><legend>Consultar objeto</legend>
<table><tr><td>Código (TN...):</td><td><input type="text" name="codigo"></td></tr>
<tr><td>ou Pedido:</td><td><input type="text" name="pedido"></td></tr>
<tr><td></td><td><input type="submit" value="Consultar"></td></tr></table></fieldset></form>""", logado=True)


@portal.post("/login")
def portal_login(usuario: str = Form(""), senha: str = Form(""), next: str = Form("")):
    if usuario != PORTAL_USUARIO or senha != PORTAL_SENHA:
        r = _pagina("Login", _form_login("Usuário ou senha inválidos.", next))
        r.status_code = 401
        return r
    token = secrets.token_hex(16)
    _sessoes[token] = usuario
    destino = next if next.startswith("/portal") else "/portal"
    resp = RedirectResponse(destino, status_code=303)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/portal")
    return resp


@portal.get("/sair")
def portal_sair(request: Request):
    _sessoes.pop(request.cookies.get(COOKIE) or "", None)
    resp = RedirectResponse("/portal", status_code=303)
    resp.delete_cookie(COOKIE, path="/portal")
    return resp


@portal.get("/rastreio", response_class=HTMLResponse)
def portal_rastreio(request: Request, codigo: str = "", pedido: str = ""):
    if not _logado(request):
        from urllib.parse import quote
        return RedirectResponse(f"/portal?next={quote(str(request.url.path) + '?' + str(request.url.query))}",
                                status_code=303)
    codigo, pedido = codigo.strip().upper(), pedido.strip().upper()
    if not (codigo or pedido):
        return _pagina("Rastreamento", '<div class="erro">Informe o código ou o pedido.</div>', logado=True)
    ev = _q("SELECT * FROM rastreio_portais_transportadoras WHERE portal='TransNorte' AND "
            "(pedido_ref=? OR codigo_rastreio=?) ORDER BY data_hora_evento", (pedido or "-", codigo or "-"))
    if not ev:
        return _pagina("Rastreamento", f"""<h2>Resultado da consulta</h2><div class="erro">Objeto
{html.escape(codigo or pedido)} não localizado na base TransNorte.</div>
<table id="eventos" class="grid"><tr><th>Data/hora</th><th>Situação</th><th>Local</th></tr></table>
<p><a href="/portal">&laquo; Nova consulta</a></p>""", logado=True)
    ref, cod = ev[0]["pedido_ref"], ev[0]["codigo_rastreio"]
    previsao = ev[-1]["previsao_entrega"] or ""
    prev_br = datetime.fromisoformat(previsao).strftime("%d/%m/%Y") if previsao else "-"
    linhas = "".join(
        f'<tr data-status="{html.escape(e["status"])}" data-hora="{html.escape(e["data_hora_evento"])}" '
        f'data-local="{html.escape(e["local"] or "")}"><td>{datetime.fromisoformat(e["data_hora_evento"]):%d/%m/%Y %H:%M}</td>'
        f'<td>{_STATUS_TXT.get(e["status"], e["status"].upper())}</td><td>{html.escape((e["local"] or "").upper())}</td></tr>'
        for e in reversed(ev))
    vencida = previsao and previsao < NOW_ENV.strftime("%Y-%m-%d") and ev[-1]["status"] != "entregue"
    alerta = (f'<div class="aviso">Previsão de entrega vencida. Para abrir ocorrência, contate a Central ou use o menu '
              f'Ocorrências.</div>') if vencida else ""
    ch = _q("SELECT protocolo, criado_em, motivo, status FROM sim_chamados_transportadora WHERE order_id=? "
            "AND transportadora='TransNorte' ORDER BY criado_em", (ref,))
    ch_html = ""
    if ch:
        ch_html = ('<h2>Ocorrências abertas</h2><table id="ocorrencias" class="grid"><tr><th>Protocolo</th><th>Aberta em</th>'
                   '<th>Motivo</th><th>Situação</th></tr>' + "".join(
                       f'<tr><td>{html.escape(c["protocolo"])}</td><td>{html.escape(c["criado_em"])}</td>'
                       f'<td>{html.escape(c["motivo"] or "")}</td><td>{html.escape(c["status"].upper())}</td></tr>'
                       for c in ch) + "</table>")
    return _pagina("Rastreamento", f"""<h2>Resultado da consulta</h2>
<table class="grid"><tr><th colspan="4">Dados do objeto</th></tr>
<tr><td>Transportadora:</td><td class="dado"><span id="transportadora">TransNorte</span></td>
<td>Código:</td><td class="dado"><span id="codigo">{html.escape(cod)}</span></td></tr>
<tr><td>Pedido embarcador:</td><td class="dado"><span id="pedido">{html.escape(ref)}</span></td>
<td>Previsão de entrega:</td><td class="dado"><span id="previsao" title="{prev_br}">{html.escape(previsao)}</span></td></tr></table>
{alerta}<h2>Histórico do objeto</h2>
<table id="eventos" class="grid"><tr><th>Data/hora</th><th>Situação</th><th>Local</th></tr>{linhas}</table>
{ch_html}<p><a href="/portal">&laquo; Nova consulta</a></p>""", logado=True)


# ------------------------------------------------------------------ export

routers: list[APIRouter] = [erp, chamados, logistica, financeiro, aprovacoes, mensagens, eventos, portal]


async def startup() -> None:
    """Garante o banco construído (e com as linhas DEMO atualizadas)."""
    from data import build_db
    await asyncio.to_thread(build_db.ensure, DB_PATH)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA journal_mode=WAL")
    finally:
        con.close()

from mock_api import reclame_aqui as _reclame_aqui  # noqa: E402  (Reclame Aqui simulado, CONTRACTS §9)
routers.extend(_reclame_aqui.routers)
