# Goxy — contratos internos (fonte da verdade para quem implementa)

Leia `../SPEC.md` para a visão. Este arquivo fixa as interfaces entre as partes, para que dados,
agentes, interface e canais sejam construídos em paralelo sem colidir. **Não mude um contrato sem
atualizar este arquivo.** Tudo em português (nomes de campos, textos da UI, mensagens).

## 0. Layout e execução

```
gohacks/
  SPEC.md
  assets/                    # mascote gerado no ComfyUI (o usuário coloca os arquivos aqui)
  data_raw/GoHacks_Talent_2026_bases_csv/{atendimento,pagamentos,transportes}/*.csv   # dados do evento (somente leitura)
  goxys/
    .venv/                   # uv venv, python 3.12 (fastapi, httpx, pydantic v2, openai, pyyaml, bs4, python-telegram-bot, slack_bolt, pytest)
    app.py                   # processo único (já existe) — NÃO reescrever, só ajustar se necessário
    core/                    # já existe: config.py, bus.py, state.py, llm.py
    data/build_db.py         # CSV -> SQLite (data/goxys.db) + casos de demo
    data/demo_cases.json
    mock_api/                # sistemas simulados; módulo mock_api/routes.py expõe `routers` e `async startup()`
    tools/                   # contratos tipados (cliente HTTP dos sistemas simulados)
    agents/                  # maestro + 7 Goxy
    policy/autonomia.yaml    # limites A..D (editável)
    api.py                   # API do orquestrador: `router` e `async startup()`
    channels/__init__.py     # `async start()` / `async stop()`; telegram.py, slack.py
    web/                     # React + Vite + Tailwind (base '/app/')
    scripts/                 # clientes sintéticos, smoke tests
    tests/
```

- Rodar: `cd goxys && .venv/bin/uvicorn app:app --port 8000 --reload`
- Instalar pacote: `uv pip install --python .venv/bin/python <pkg>`
- Painel em dev: `cd goxys/web && npm run dev` (porta 5173, proxy de `/api`, `/portal`, `/assets-goxys` para 8000).
- Painel compilado: `npm run build` → `web/dist`, servido pelo FastAPI em `/app/`.
- Assets do mascote servidos em `/assets-goxys/<arquivo>` (pasta `gohacks/assets`).
- "Agora" do ambiente: **2026-11-02 08:00** (`core.config.NOW_ISO`).

## 1. Banco (SQLite `goxys/data/goxys.db`)

`data/build_db.py` (idempotente, roda em < 30 s) carrega **todos os CSVs de `atendimento/`** em tabelas
com o mesmo nome do arquivo (ex.: `pedidos`, `clientes`, `anexos_conversas`, `rastreio_portais_transportadoras`,
`fila_aberta`, `historico_conversas`, `historico_mensagens`, `trocas_reenvios`, `estornos`, `politicas`,
`ferramentas`, `chamados_transportadora`, `equipe_atendimento`, ...) com índices nas chaves
(`order_id`, `customer_id`, `conversation_id`, `hash_perceptual`, `pedido_ref`, `codigo_rastreio`).
Também carrega `transportes/transportadoras.csv` como `transportadoras`.

Tabelas operacionais criadas vazias (escritas pelos sistemas simulados):
`sim_chamados`, `sim_trocas`, `sim_chamados_transportadora`, `sim_reembolsos`, `sim_aprovacoes`,
`sim_mensagens`, `sim_eventos` (log de ferramentas). Mais `goxys_cases` (criada por core/state.py).

### Casos de demo — `data/demo_cases.json`

Lista com 5 objetos (SPEC §8). Use **clientes e pedidos reais da base** quando possível; quando precisar
de um cenário que a base não tem, insira linhas novas (prefixo `DEMO`) nas tabelas reais pelo build_db.

```json
{
  "n": 1,
  "titulo": "Avaria legítima de baixo valor",
  "esperado": {"nivel": "A", "acao": "reenvio", "status_final": "resolvido"},
  "canal": "whatsapp",
  "customer_id": "CH...", "order_id": "GCH...", "conversation_id": "DEMO-AT001",
  "mensagem": "Oi! Meu copo chegou com a tampa rachada, mandei a foto. Pedido GCH...",
  "anexos": [{"anexo_id": "DEMO-IMG01", "tipo": "foto_produto", "hash_perceptual": "...", "exif_data_captura": "...",
              "exif_modelo_camera": "...", "exif_software": "", "descricao_visual": "..."}]
}
```

Os 5 casos:
1. Avaria legítima de baixo valor (≤ R$150, foto inédita, EXIF depois da entrega, cliente limpo) → **A**, reenvio.
2. **Foto reaproveitada (caso real de 17/10)** — replay da conversa real **AT114498** (WhatsApp, cliente CH585887
   "Joana", pedido GCH551285 "Case Infinite Air / iPhone 17 Pro Max Xadrez Vichy", R$ 97,80, entregue 13/10).
   `mensagem` = as 2 mensagens reais da cliente; anexo = o registro real **IMG000267** com todos os campos do CSV
   (nada inventado; `replay_real` guarda as mensagens e o desfecho real: reenvio OMS000514, R$ 45,05). Sinais reais:
   hash `41b2c50cabf85942` já enviado em 08/09 por OUTRO cliente (IMG000123, CH999406, GCH535078, reclame_aqui);
   EXIF 03/10 (antes do pedido e da entrega); a foto mostra "capinha transparente com estampa", sem o Xadrez Vichy
   → `produto_confere=false`. Três sinais objetivos → **D / negar / rejeitado** (não C: não há ambiguidade a aprovar).
3. Atraso causado por transportadora: pedido `em_transito` com `data_prometida` vencida; carrier **TransNorte**
   (a transportadora SEM API: rastreio → HTTP 501 → agente usa o portal) → abre chamado na transportadora e avisa → **A**.
4. Reembolso alto (> R$500) com foto duvidosa (EXIF ausente, descrição ambígua, score 40–70) → **C**, aprovação no Slack/Telegram/painel.
5. Ameaça de processo + cita Reclame Aqui/Procon → **D**, humano assume, ações congeladas.

Escolhidos (ver `sinais` de cada caso no JSON): 1 = CH971787/GCH559588, 2 = CH585887/GCH551285 (conversa real
AT114498, anexo real IMG000267; `conversation_id` = "AT114498"), 3 = CH107459/GCH558661 (TransNorte), 4 = CH384938/**GBH990004**
(pedido inserido pelo build_db; usa o padrão `GBH` + 6 dígitos e não `DEMO`, para não quebrar regex de pedido),
5 = CH590826/GCH547832. Os anexos DEMO-IMG0x também são inseridos em `anexos_conversas`; anexos reais (sem prefixo
`DEMO`, ex.: IMG000267) não são duplicados.

Perícia (`/api/erp/pericia-foto`): `reuso` só conta anexos com o mesmo hash enviados **antes** do `enviado_em` do
anexo analisado (nunca ele mesmo); a perícia de IMG000123 dá `reuso=[]`, a de IMG000267 dá `[IMG000123]`. Estampa de
catálogo do pedido (`ESTAMPAS`, ex.: "Xadrez Vichy") ausente da descrição da capinha → `produto_confere=false`.

### Insight `fotos_reaproveitadas` (Analista, `agents/analista.py`)

Auditoria retroativa calculada dos dados (anexos não-DEMO cujo hash já foi enviado antes por outro cliente;
pago = `trocas_reenvios.custo_total` + `estornos.valor` pela `conversation_id`). Vai em `insights()["fotos_reaproveitadas"]`
e é o **primeiro** item de `insights()["clusters"]` (traz também os campos de cluster: `id="fotos-reaproveitadas"`,
`tipo`, `chave`, `tamanho`, `custo_estimado_rs`, `causa_provavel`, `acao_preventiva`, `contatos_evitaveis=0`).
Hoje: 8 hashes, 9 reusos, 9 pagos, R$ 527,24.

```json
{"titulo": "9 fotos reaproveitadas foram pagas", "reusos": 9, "hashes": 8, "clientes_distintos": 17, "pagos": 9,
 "custo_rs": 527.24,
 "casos": [{"hash": "41b2c50cabf85942",
            "original": {"anexo_id": "IMG000123", "customer_id": "CH999406", "order_id": "GCH535078",
                         "enviado_em": "2026-09-08 12:36:44", "canal": "reclame_aqui"},
            "reusos": [{"anexo_id": "IMG000267", "customer_id": "CH585887", "order_id": "GCH551285",
                        "enviado_em": "2026-10-17 16:39:59", "canal": "whatsapp", "pago_rs": 45.05,
                        "conversation_id": "AT114498"}]}],
 "acao": "Detetive teria barrado os 9 antes do reenvio"}
```

## 2. Sistemas simulados (HTTP, base `core.config.SYSTEMS_BASE_URL`)

Todas as respostas JSON. Erros: `{"erro": "<codigo>", "mensagem": "..."}`. Toda escrita grava em `sim_eventos`
e aceita header `X-Agente` (quem pediu) e `Idempotency-Key`.
O caso vem em `case_id` no corpo ou no header `X-Caso`. Campos extras (não quebram o contrato): perícia devolve
também `score_veracidade` (0..100) e `sinais: [{sinal, peso, detalhe}]`; cliente devolve `motivos_risco`;
rastreio devolve `dias_uteis_atraso` (`dias_atraso` = dias corridos) e `chamados_transportadora`; reembolso devolve
422 `valor_acima_do_pedido` se passar do valor pago.

| Método e rota | Corpo / query | Resposta |
| --- | --- | --- |
| GET `/api/erp/pedidos/{order_id}` | | pedido (colunas de `pedidos`) + `itens` (itens_pedido) + `pagamentos` (pagamentos_gateway). 404 se não existe |
| GET `/api/erp/clientes/{customer_id}` | | cliente (colunas de `clientes`, sem CPF completo: mascarar) + `flag_risco` (bool) + `qtd_reclamacoes_90d` |
| GET `/api/erp/clientes/{customer_id}/historico` | | `{pedidos:[], conversas:[], trocas:[], estornos:[], anexos:[], reclame_aqui:[]}` |
| GET `/api/erp/rastreio/{order_id}` | | `{transportadora, codigo_rastreio, status, previsao_entrega, data_prometida, atrasado: bool, dias_atraso, eventos:[{data_hora, status, local}]}`. **TransNorte → 501** `{"erro":"sem_api","portal":"/portal","mensagem":"TransNorte não tem API de rastreio"}` |
| GET `/api/erp/anexos?conversation_id=&order_id=&hash=` | | lista de anexos |
| POST `/api/erp/pericia-foto` | `{order_id, customer_id, anexo: {Anexo}}` | `{avaria_detectada: bool, confianca: 0..1, reuso: [{anexo_id, order_id, customer_id, enviado_em, descricao_visual}], exif: {presente, data_captura, antes_da_entrega: bool, software_suspeito: bool, modelo}, indicios_ia: [str], produto_confere: bool, descricao_visual}` |
| POST `/api/chamados` | `{conversation_id, customer_id, order_id, canal, assunto}` | `{chamado_id}` (chamado interno de atendimento) |
| PATCH `/api/chamados/{id}` | `{status, nota}` | chamado |
| GET `/api/chamados/{id}` | | chamado + histórico |
| POST `/api/logistica/trocas` | `{order_id, sku, tipo: reenvio\|troca\|nova_producao, motivo, quantidade}` | `{solicitacao_id, pedido_reposicao_id, custo_total, status}` |
| POST `/api/logistica/chamado-transportadora` | `{order_id, motivo}` | `{chamado_id, protocolo, transportadora, prazo_resposta}` |
| POST `/api/financeiro/reembolsos` | `{order_id, valor, motivo, aprovacao_id?}` + `Idempotency-Key` | `{reembolso_id, valor, status}`. **valor > 500 sem aprovação aprovada → 403** `{"erro":"exige_aprovacao"}`. Mesma chave → mesma resposta, nunca paga 2× (`"idempotente": true`) |
| POST `/api/aprovacoes` | `{case_id, titulo, resumo, nivel, valor, acao_proposta, opcoes:["aprovar","negar","ajustar"], evidencias:[], prazo_s}` | `{aprovacao_id, status:"pendente", expira_em}` |
| GET `/api/aprovacoes?status=pendente` | | lista |
| GET `/api/aprovacoes/{id}` | | aprovação |
| POST `/api/aprovacoes/{id}/decisao` | `{decisao: aprovar\|negar\|ajustar, aprovador, canal: painel\|slack\|telegram, ajuste?: {valor?, acao?, nota?}}` | aprovação atualizada (409 se já decidida). **Ao decidir, publica no bus `approval.decided`** — é isso que retoma o caso |
| POST `/api/mensagens` | `{canal, customer_id, conversation_id, case_id, texto}` | `{message_id, enviado_em, canal}`; publica `message.sent` |
| GET `/api/mensagens?conversation_id=` | | lista |
| GET `/api/eventos?case_id=` | | log de ferramentas (sim_eventos) |
| GET `/portal` | | portal web da TransNorte (HTML), login `goxys` / `hackathon`, sessão por cookie |
| POST `/portal/login` | form `usuario`, `senha` | redirect + cookie |
| GET `/portal/rastreio?codigo=` ou `?pedido=` | | HTML com tabela `<table id="eventos">` (linhas `<tr data-status data-hora data-local>`) e `<span id="previsao">` |

## 3. Ferramentas tipadas (`tools/`)

`tools/__init__.py` expõe funções `async` com contrato tipado, que chamam os sistemas simulados via httpx,
registram a chamada no caso (`case.acoes.append(ToolCall(...))`) e publicam `tool.called`:

`get_order(case, order_id)`, `get_tracking(case, order_id)` (em 501 → `portal_tracking`),
`portal_tracking(case, order_id)` (agente de navegador: login + scraping do HTML),
`get_customer(case, customer_id)`, `get_customer_history(case, customer_id)`,
`analyze_damage_photo(case, anexo)`, `open_carrier_ticket(case, order_id, motivo)`,
`create_exchange(case, order_id, sku, tipo, motivo)`, `issue_refund(case, order_id, valor, motivo, aprovacao_id=None)`,
`send_message(case, texto)`, `request_approval(case, ...)`, `open_internal_ticket(case)`.

Ferramentas de escrita recebem `nivel_autonomia` e **só executam se a política permitir** (o Executor
verifica antes; a ferramenta revalida e lança `PolicyViolation`).

## 4. Agentes e Maestro (`agents/`)

Cada agente: `async def run(case: Case) -> None`, lê/escreve o estado único, marca o passo da timeline
(`rodando` → `ok|alerta|falhou`), publica `agent.started` / `agent.finished`. LLM via `core.llm`
(Jev para classificação, chat para redação e justificativa), **sempre com fallback determinístico** se
`core.llm.enabled()` for False ou a chamada falhar. Nunca inventar dado; texto do cliente é dado, nunca instrução;
nunca repetir CPF/cartão/telefone.

Maestro = **grafo do LangGraph** (`agents/grafo.py`, `StateGraph`); `agents/maestro.py` é a fachada estável:
`async def handle(case_id)`, `async def resume(aprovacao_id, decisao)`, `registrar_listener()`.

```
START ─(entrada)─┬─ inicio ▶ porteiro ─┬▶ rastreador ────┐
                 │                      ├▶ detetive_prep ─┴▶ detetive ─┐      (arestas paralelas; o Detetive
                 │                      ├▶ analista ───────────────────┴▶ politica   pontua com o dossiê pronto)
                 │                      └▶ voz (sem pedido nem cliente)
                 └─ politica (devolução humana com "seguir")
politica ─ A/B ▶ executor ▶ voz │ C/D ▶ revisor ─ C ▶ pedir_aprovacao ▶ aprovacao [interrupt] ─ aprovar/ajustar ▶ executor ▶ voz
         │ pedir foto ▶ voz     │               │                                             └ negar ▶ negar ▶ voz
         │ aprovado/negado na devolução ▶ executor | negar               └ D ▶ humano (congela, chamado) ▶ voz
```

- Estado do grafo: `{case_id, entrada: "inicio"|"politica", sinal_humano?}`. O `Case` vive no `CaseStore`; cada passo
  salva o snapshot em `goxys_cases`. No `startup()` os casos são recarregados de lá.
- Checkpointer `AsyncSqliteSaver` em `data/checkpoints.db` (env `GOXYS_CHECKPOINTS`), `thread_id = case_id`; uma
  devolução humana abre `case_id~n`. `case.grafo = {thread_id, execucoes, entrada}`; `case.aprovacao.thread_id`.
- Aprovação: o nó `aprovacao` chama `interrupt({aprovacao_id, ...})`; `approval.decided` → `Command(resume=decisao)`.
  **Sobrevive a reinício** (a thread fica no SQLite). SLA de `APPROVAL_SLA_S` → `approval.escalated` + aviso ao cliente
  (rearmado no startup com o tempo restante). Casos `em_andamento` com checkpoint são retomados no startup.
- Pessoa com o caso (`case.responsavel`): todos os nós viram no-op e as rotas vão a END; ferramentas de escrita
  recusam (`PolicyViolation`); a Voz não fala pela pessoa. Uma aprovação decidida nesse meio-tempo é registrada,
  mas só executa depois da devolução.
- GET `/api/goxys/grafo` devolve o grafo compilado (`draw_mermaid()`), não um desenho.

Toda decisão (`case.decisao`) guarda `nivel, acao, valor, motivo, regra, porque (≤ 140, frase humana), sinais
(com custo_erro), justificativa, politica_versao, revisor (C/D)`. `case.sugestao_responsavel = {colaborador_id, nome,
cargo, motivo}` nos níveis C (supervisor) e D (analista).

Política (`policy/autonomia.yaml` + `agents/politica.py`): `decide(case) -> {nivel: A|B|C|D, motivo, sinais, politica_versao}`.
Limites da SPEC §6, ajustados pelos dados (`parametros.md`: atendente reenvia até R$200, reembolsa até R$100) e custo
de erro assimétrico (SPEC §14 ideia 2). Revisor maker-checker nos níveis C/D (SPEC §14 ideia 6).

## 5. API do orquestrador (`api.py`, prefixo `/api/goxys`)

| Rota | Faz |
| --- | --- |
| POST `/api/goxys/casos` | `{canal, mensagem, customer_id?, order_id?, conversation_id?, anexos?, telegram_chat_id?, confirmacao?}` (`confirmacao` = texto que o canal já mandou ao cliente; senão o Goxy manda a sua) → cria caso e dispara o Maestro em background → `{case_id}` |
| POST `/api/goxys/simular/{n}` | dispara o caso de demo n (1..5) → `{case_id}` |
| POST `/api/goxys/simular/todos` | dispara os 5 com 1,5 s de intervalo |
| POST `/api/goxys/fila/importar?limite=10` | cria casos a partir de `fila_aberta` (sem processar; status `novo`) |
| POST `/api/goxys/casos/{id}/processar` | roda o Maestro num caso `novo` |
| POST `/api/goxys/casos/{id}/desfazer` | `{por, canal?}` → nível B, até 15 min depois da ação (relógio real, `decisao.desfazer_ate`): cancela/estorna de verdade as escritas do caso (`/api/logistica/trocas/{id}/cancelar`, `/api/financeiro/reembolsos/{id}/estornar`), grava `decisao.desfeito = {por, canal, ts, acoes:[{ferramenta, id, status}]}`, passo na timeline, avisa o cliente, status `humano`, evento `case.undone` → `{case_id, desfeito, status}`. Idempotente. 409 `{erro:"prazo_expirado"}` fora da janela; 409 `{erro:"nada_a_desfazer"}` se não é nível B |
| GET `/api/goxys/casos` | lista resumida `[{id, canal, titulo, status, nivel, cliente_nome, criado_em, mascot_state, demo_caso}]` |
| GET `/api/goxys/casos/{id}` | Case completo (model_dump) |
| GET `/api/goxys/stream?since=0` | SSE; cada mensagem `data: <evento JSON>` (formato de core/bus.py) |
| GET `/api/goxys/metricas` | `{total, autonomos_pct, tempo_medio_s, fraude_evitada_rs, fraudes_barradas, aprovacoes_pendentes, tempo_medio_aprovacao_s, contatos_evitados, por_nivel:{A,B,C,D}, por_status:{}, llm:{chamadas,tokens,usd}, baseline:{tempo_medio_humano_min, custo_medio_conversa}}` |
| GET `/api/goxys/analista` | insights do Analista de causa-raiz sobre o histórico (clusters transportadora×UF, impressora, lote, cobranças em dobro) |
| GET `/api/goxys/placar` | último placar dos clientes sintéticos (`data/placar.json`) |
| GET `/api/goxys/mascote` | manifesto dos assets: `{agentes:{porteiro:"/assets-goxys/..."}, estados:{investigando:"...webm"}}` (varre a pasta assets) |
| GET `/api/goxys/saude` | `{ia: bool, telegram: bool, slack: bool, db: bool, politica_versao}` |
| GET `/api/goxys/grafo` | `{mermaid, nos:[{id, nome}], arestas:[{de, para, condicional}], checkpointer, checkpoints}` (grafo real do LangGraph) |
| GET `/api/goxys/equipe` | `[{colaborador_id, nome, cargo, nivel, canais_atendidos:[str], supervisor_id, status}]` (só ativos, de `equipe_atendimento`) |
| POST `/api/goxys/casos/{id}/passar` | `{para: colaborador_id, nota?}` → `case.responsavel = {colaborador_id, nome, cargo, desde, nota}`, status `humano`, mascote `humano`, ações do Goxy congeladas (aprovação pendente continua válida), `case.resumo_passagem` (str, 3 linhas: "Cliente quer: …" / "Goxy achou: …" / "Falta decidir: …"), passo "Maestro passou o caso para <nome>", evento `case.handoff` → `{case_id, status, responsavel, resumo_passagem}`. 422 colaborador inválido |
| POST `/api/goxys/casos/{id}/mensagens` | `{texto, autor: colaborador_id}` → envia ao cliente pelo canal de origem (`/api/mensagens`; Telegram via `telegram_chat_id`), acrescenta `{autor:"agente", nome, colaborador_id, texto, ts, canal, message_id}` em `case.mensagens`; `message.sent` |
| POST `/api/goxys/casos/{id}/cliente` | `{texto, anexos?}` → mensagem nova do cliente num caso aberto (o Telegram usa quando o caso está com `responsavel`); `{autor:"cliente", texto, ts}` em `case.mensagens`; evento `message.received` |
| POST `/api/goxys/casos/{id}/sugerir-resposta` | → `{texto}`: rascunho da Voz para a pessoa (não envia) |
| POST `/api/goxys/casos/{id}/devolver` | `{nota?, instrucao: "seguir"\|"encerrar"}` → limpa `responsavel`, passo "<nome> devolveu o caso", evento `case.returned`. "seguir": o grafo retoma a partir da política com a nota como sinal humano ("pode fazer o reembolso" → aprovação humana registrada em `/api/aprovacoes`, `case.aprovacao.via = "devolucao"`; "não procede" → negativa). "encerrar": status `resolvido`. 409 se o caso não está com uma pessoa |
| POST `/api/goxys/demo/sla` | `{segundos}` → muda o SLA das aprovações em tempo de execução (demo) e rearma as pendentes → `{sla_s, aprovacoes_rearmadas}`. Ao estourar: `approval.escalated {aprovacao_id, para:{colaborador_id, nome}, novo_prazo}`, `aprovacao.escalada_para = {colaborador_id, nome, em, novo_prazo}` (outro supervisor, nunca o sugerido nem quem pediu), passo "Mensageiro escalou para <nome>", uma única mensagem ao cliente (`situacao: "sla"`, "seu caso está com um supervisor; resposta até HH:MM"). A aprovação continua decidível |
| GET `/api/goxys/aprendizado` | `{total_decisoes, concordancia_pct, por_nivel:{C:{total, concordou, concordancia_pct}}, divergencias:[{case_id, aprovacao_id, recomendado, decidido:{decisao, ajuste}, aprovador, nota}], sugestoes:[Sugestao], historico_real:[{acao, faixa:[de, ate], executadas, com_aprovacao, supervisores, fonte}], politica_versao}`. Concordância = aprovações humanas da sessão (aprovador automático dos sintéticos fica fora); nível B desfeito também é divergência. `historico_real` vem de `acoes_executadas_sistemas.aprovado_por` (+ total de `trocas_reenvios.aprovado_por`) |
| POST `/api/goxys/aprendizado/sugestoes/{id}/validar` | dispara job em background: os 20 clientes sintéticos com a política proposta, em memória e só nos casos da validação (header interno `X-Goxy-Politica`; esses casos ficam fora da fila e das métricas e são apagados no fim). Não toca no YAML → `Job` |
| GET `/api/goxys/aprendizado/jobs/{job}` | `Job = {job_id, sugestao_id, status: rodando\|concluido\|falhou, feitos, total, placar:{acertos,total}, erros:[{persona, falhas, obtido}], politica_versao, sugestao}`. A sugestão vira `validada` só com 20/20; senão `reprovada` (erros em `sugestao.validacao`) |
| POST `/api/goxys/aprendizado/sugestoes/{id}/aplicar` | `{por}` → só se `validada` (senão 409 `nao_validada`): grava a nova versão do YAML (`-rN+1`, comentários preservados), guarda a anterior em `policy/historico/autonomia-<versao>.yaml`, recarrega a política, publica `policy.updated`; decisões seguintes registram a versão nova |
| POST `/api/goxys/demo/limpar` | apaga todos os casos (memória, `goxys_cases`, checkpoints), as escritas `sim_*` dos pedidos de demo e as aprovações pendentes; limpa o histórico do barramento e publica `demo.reset`. Não reconstrói o banco |

`case.mensagens` traz a conversa completa, em ordem: `{autor: "cliente"|"goxys"|"agente", nome?, texto, ts, canal,
tipo?: "confirmacao", situacao?, message_id?}` (mensagem inicial, confirmação imediata, respostas da Voz, mensagens
humanas). `case.aprovacao = {aprovacao_id, status: pendente|aprovada|negada|ajustada, decisao, aprovador, canal,
titulo, resumo, tempo_resposta_s, thread_id}`; o título é curto e cita o valor uma vez ("Reembolso de R$ 689,70 com
foto duvidosa"), o valor vai também em `valor`. `veracidade.comparacao = {original, atual, similaridade, diferencas,
indicios_ia, outros_usos:[{anexo_id, order_id, customer_id, enviado_em}]}`.

Aprovação de qualquer canal usa a mesma rota: POST `/api/aprovacoes/{id}/decisao` `{decisao, aprovador, aprovador_id?, canal, ajuste?:{valor?, acao?, nota?}}`.
- **Segregação** → 403 `{erro:"segregacao"}`: o aprovador é quem pediu a ação (`solicitante`) — "Você pediu essa ação; outra pessoa precisa aprovar." — ou é o `responsavel` do caso — "Este caso está com você; outra pessoa precisa aprovar."
- **Alçada** → 403 `{erro:"alcada", cargo, limite}` (aprovar/ajustar; vale o valor ajustado): o aprovador é identificado por `aprovador_id` ou pelo `aprovador` (id ou nome em `equipe_atendimento`). Atendente e analista: até a alçada do atendente em `politicas.csv` (reembolso R$ 100; reenvio, reimpressão e troca R$ 200); supervisor: sem limite; cosmetovigilância: só supervisor. Aprovador fora da equipe (ex.: usuário do Slack/Telegram) não é barrado pela alçada.
- **Recomendação e concordância**: toda aprovação guarda `recomendacao = {acao, valor, decisao_sugerida: aprovar|negar, motivo}`; ao decidir grava `concordou` (`ajustar` conta como divergência), `aprovador_id`, `aprovador_cargo`. `approval.decided` leva os mesmos campos; o Case espelha em `case.aprovacao`.
- **Ajustar**: o Executor usa `ajuste.valor`/`ajuste.acao` com a mesma `aprovacao_id` (`decisao.valor_original`, `decisao.acao_original`); a Voz responde com o valor ajustado.
- **Devolução com nota de aprovação**: se quem devolveu não pode aprovar (alçada/segregação), a aprovação fica pendente para outra pessoa (`aprovacao.bloqueio` com a mensagem).

`Sugestao = {id: "S-reenvio-200-300", regra: "acoes.reenvio.limite_papel_rs", de, para, evidencia: "Supervisores aprovaram 45 de 45 reenvios entre R$ 200 e R$ 300 (base real)", ressalva, impacto, status: proposta|validando|validada|reprovada|aplicada, validacao?: {job_id, placar, erros, em}, aplicada?: {por, em, versao, anterior}}`. Critério: sobe um degrau o limite da ação quando os supervisores reais aprovaram 100% da faixa logo acima (≥ 10 casos) e ninguém negou nessa faixa na sessão. Registro em `data/sugestoes_politica.json`.

Sistemas simulados novos (idempotentes): POST `/api/logistica/trocas/{solicitacao_id}/cancelar` → `{solicitacao_id, pedido_reposicao_id, status:"cancelado", idempotente}`; POST `/api/financeiro/reembolsos/{reembolso_id}/estornar` → `{reembolso_id, valor, status:"estornado", idempotente}`.

## 6. Eventos do barramento (SSE)

Formato: `{id, type, ts, case_id, agent, data}`.

| type | data |
| --- | --- |
| `case.created` / `case.updated` | `{case: Case}` (snapshot completo) |
| `agent.started` | `{titulo}` |
| `agent.finished` | `{status: ok\|alerta\|falhou, titulo, detalhe, evidencias, duracao_ms}` |
| `tool.called` | `{ferramenta, tipo, parametros, status_http, resultado_resumo, duracao_ms}` |
| `llm.call` | `{kind, model, tokens_in, tokens_out, usd, ms}` |
| `policy.decided` | `{nivel, acao, valor, motivo, porque, sinais, politica_versao}` |
| `approval.requested` | `{aprovacao_id, titulo, resumo, nivel, valor, acao_proposta, evidencias, expira_em}` |
| `approval.decided` | `{aprovacao_id, decisao, aprovador, canal, ajuste, tempo_resposta_s}` |
| `approval.escalated` | `{aprovacao_id, para: {colaborador_id, nome}, novo_prazo}` |
| `message.sent` | `{canal, texto, message_id, customer_id, telegram_chat_id?}` |
| `mascot.state` | `{state: parado\|investigando\|fraude\|esperando\|resolvido\|humano}` |
| `case.closed` | `{status, resumo}` |
| `case.handoff` | `{para, nome, cargo, resumo, nota}` |
| `case.returned` | `{por, nome, nota, instrucao, sinal}` |
| `message.received` | `{canal, texto, autor: "cliente", customer_id}` |
| `demo.reset` | `{casos_apagados, apagado}` |
| `case.undone` | `{por, canal, acoes:[{ferramenta, id, status}]}` |
| `policy.updated` | `{versao, anterior, regra, de, para, por, sugestao_id, historico}` |
| `aprendizado.job` | `{job_id, sugestao_id, status, feitos, total, placar, erros}` |

Canais escutam via `bus.subscribe_listener`: `approval.requested` → posta com botões no Slack/Telegram;
`message.sent` com `telegram_chat_id` → envia ao cliente no Telegram; `approval.decided` → edita a mensagem.

## 7. Mascote

O usuário está gerando os personagens e vai colocar em `gohacks/assets/`. A interface descobre os arquivos via
`GET /api/goxys/mascote`, que casa por nome de arquivo (case-insensitive, contém): nomes de agente
(`maestro, porteiro, rastreador, detetive, executor, voz, mensageiro, analista`) e de estado
(`investigando, fraude, esperando, resolvido, humano`), extensões `.webm .mp4 .png .webp .jpg .gif`.
Sem arquivos → avatar desenhado (SVG/CSS) por agente. Nunca quebrar se a pasta estiver vazia.

## 8. Visual (SPEC §9 e §18)

Tokens em `web/src/tokens.css` com os nomes da SPEC (`--marca`, `--marca-suave`, `--fundo`, `--superficie`,
`--borda`, `--texto`, `--texto-suave`, `--ok`, `--espera`, `--critico`, `--raio`), claro e escuro.

**Identidade real do Gogroup**, extraída do CSS de https://gogroup-hacks-talent.devgogroup.com (substitui a paleta provisória):

| Token Goxy | Claro | Escuro | Origem no site |
| --- | --- | --- | --- |
| `--fundo` | `#eef3fb` | `#070f26` | `--bg` / `--ink` |
| `--superficie` | `#ffffff` | `#0d1a3d` | `--panel` |
| `--superficie-2` | `#e4ecf8` | `#13234d` | `--panel-2` |
| `--borda` | `#c6d4ee` | `#243a73` | `--line` |
| `--texto` | `#0a1633` | `#ffffff` | `--fg` |
| `--texto-suave` | `#4a5a82` | `#a9b8dc` | `--muted` |
| `--marca` (acento "agora", botão Aprovar) | `#e61782` | `#e61782` | `--pink` |
| `--estrutura` (links, seleção) | `#2659a5` | `#3dbfef` | `--blue` / `--cyan` |
| `--ok` | `#13865b` | `#3ddc97` | (site não tem verde) |
| `--espera` | `#f8ae13` (fundo) / `#9a6a00` (texto) | `#f8ae13` | `--orange` |
| `--critico` | `#e5381a` | `#ff6b4f` | `--red` |
| cores de agente | ciano `#3dbfef`, rosa `#e61782`, laranja `#f8ae13`, amarelo `#d7d900`, azul `#2659a5` | | cores das "classes" |

Fonte: **Poppins** (400–800) para tudo; JetBrains Mono só para dados literais (IDs, protocolos, hash). Raios do site: 18 / 28 / 40 px.

## 9. Reclame Aqui simulado

Módulos: `mock_api/reclame_aqui.py` (API + portal; anexado a `mock_api.routes.routers`) e `channels/reclame_aqui.py`
(adaptador, ligado sempre por `channels.start()`). Lê as reclamações reais (`reclame_aqui`; texto = 1ª mensagem do
cliente em `historico_mensagens`). Tabelas próprias (criadas com IF NOT EXISTS): `sim_ra_respostas`
(respostas `publica`/`privada`) e `sim_ra_novas` (reclamações da demo, `origem=simulada`, e vínculo reclamação ↔
`case_id`, `origem=fila`). Mesmo padrão de erros, `X-Agente`, `Idempotency-Key` e log em `sim_eventos`.
Trocar pela integração oficial = apontar `RECLAME_AQUI_API` (env do adaptador) para a URL oficial + autenticação.

| Método e rota | Corpo / query | Resposta |
| --- | --- | --- |
| GET `/api/reclameaqui/reclamacoes` | `?status=&desde=&empresa=&case_id=&importacao=pendente&limite=` | lista `[{reclamacao_id, origem: base\|simulada, empresa, titulo, texto, cidade, uf, data_publicacao, status_publico, data_primeira_resposta_empresa, resolvido, nota_consumidor, voltaria_a_fazer_negocio, customer_id, order_id, conversation_id, case_id, respostas_publicas, respondida_por_goxys}]`, mais recentes primeiro |
| GET `/api/reclameaqui/reclamacoes/{id}` | | reclamação + `historico_publico: [{autor: consumidor\|empresa, data, texto, origem: base\|goxys, selo?}]` + `mensagens_privadas`. 404 `reclamacao_nao_encontrada` |
| POST `/api/reclameaqui/reclamacoes/{id}/resposta-publica` | `{texto, aprovacao_id, case_id?}` | `{resposta_id, tipo:"publica", texto, publicado_em, selo:"Respondida pela empresa"}`. **Sem `aprovacao_id` com status aprovada/ajustada → 403** `{"erro":"exige_aprovacao"}` |
| POST `/api/reclameaqui/reclamacoes/{id}/mensagem-privada` | `{texto, case_id?}` | `{resposta_id, tipo:"privada", ...}` |
| GET `/api/reclameaqui/reputacao` | `?empresa=Gocase` (`todas` = grupo) | `{total_reclamacoes, pct_respondidas, pct_resolvidas, pct_voltaria_a_fazer_negocio, nota_media_consumidor, tempo_medio_primeira_resposta_h, tempo_medio_primeira_resposta_txt, respostas_goxys, indice_reputacao (0..10 = resp×2+nota×3+resolv×3+voltaria×2), classificacao}`; recalculada com as respostas do Goxy |
| POST `/api/reclameaqui/simular-nova` | `{titulo, texto, customer_id?, order_id?, empresa?}` | reclamação nova (`status_publico: "Não respondida"`); publica `ra.nova` |
| POST `/api/reclameaqui/importar-fila?limite=5` | | enfileira reclamações reais da `fila_aberta` (canal reclame_aqui) → `{enfileiradas:[id], total}`; publica `ra.nova` |
| POST `/api/reclameaqui/reclamacoes/{id}/vinculo` | `{case_id}` | lado Goxy (não existe na API oficial): grava o vínculo |
| GET `/reclameaqui?empresa=Gocase&status=` | | portal HTML "Reclame Aqui (simulado)": reputação (`#reputacao`) + lista (`#reclamacoes`) |
| GET `/reclameaqui/reclamacao/{id}` | | HTML da reclamação com histórico público; respostas do Goxy com selo "Respondida pela empresa" |

Adaptador: **entrada** = polling a cada 20 s (e na hora, ao ouvir `ra.nova`) de `importacao=pendente` → POST
`/api/goxys/casos` `{canal:"reclame_aqui", mensagem: titulo + texto, customer_id, order_id, conversation_id}` + vínculo.
**Saída** = `message.sent` com `canal == "reclame_aqui"`. Confirmação, status/SLA, pedido de foto/número e
"especialista assume" vão **só como mensagem privada, sem aprovação**. Só a resposta final (situação `resolvido` ou
`negado`) vira resposta pública. Classificação, nesta ordem: `tipo`/`situacao` no evento (se vier); a mensagem no caso
(GET `/api/goxys/casos/{id}`, casada por `message_id`: a Voz grava `tipo: "confirmacao"` ou `situacao`); heurística
de texto (`recebi sua mensagem`, `em análise`, `te aviso aqui`, `me manda uma foto`, `especialista`… → privada).
Resposta final → POST `/api/aprovacoes` (titulo "Publicar resposta pública
no Reclame Aqui", resumo = texto sanitizado, nível C, `acao_proposta: "publicar_resposta_publica_ra"`,
`evidencias:[{reclamacao_id, order_id}]`) → em `approval.decided` aprovar/ajustar (texto final = `ajuste.nota`, se houver)
publica a resposta pública e depois a mensagem privada com os detalhes do pedido; negar → só registra.
CPF, cartão e telefone são removidos por regex antes de pedir a aprovação e antes de publicar.

| Evento | data |
| --- | --- |
| `ra.nova` | `{reclamacao_id \| reclamacao_ids, titulo?, origem: simulada\|fila}` |
| `ra.publicada` | `{reclamacao_id, aprovacao_id, resposta_id, texto, dados_removidos, url}` |
| `ra.privada` | `{reclamacao_id, resposta_id, texto, motivo?: "confirmacao_ou_status"}` |
| `ra.negada` | `{reclamacao_id, aprovacao_id, aprovador}` |

## 8b. Manual de marca oficial do Gogroup (assets/branding.pptx) — substitui a §8 onde conflitar

Fonte: manual "Identidade Visual / Logo e variações / Paleta de cores" do Gogroup, entregue pelo usuário.

- **Paleta oficial (não usar cores fora dela em elementos de marca):** azul `#2659a5` (cor PRINCIPAL, sempre protagonista),
  vermelho `#e5381a`, rosa `#e61782`, vermelho-carmim `#e5273c`, amarelo-limão `#d7d900`, laranja `#f8ae13`, ciano `#3dbfef`, branco `#ffffff`.
  Neutros usados no próprio manual: fundo `#f5f5f5`, texto `#333333`.
- **Contraste:** nunca texto branco sobre o limão ou o laranja; não colocar laranja, vermelho e rosa lado a lado (vibram).
- **Tipografia:** Poppins — ExtraBold nos títulos, SemiBold em subtítulos, Regular no texto.
- **Linguagem visual:** minimalismo + color blocking, cantos bem arredondados, grandes painéis azuis, moldura/etiquetas em pílula
  limão com texto azul, botão circular com seta (`→` dentro de um círculo).
- **Logo:** wordmark "gogroup" em minúsculas (`assets/marca/gogroup-logo.png`, `-branco.png`, `-tagline*.png` com "construindo o futuro")
  e o "G" reduzido num círculo azul (`gogroup-g.png`), só quando o logo não cabe. Nunca mudar a cor (só azul ou branco),
  distorcer, girar, aplicar sombra/efeito/estampa. Área de proteção = altura do "o"; tamanho mínimo digital 35 px de largura.
- **Grafia:** sempre "Gogroup" (G maiúsculo, resto minúsculo). Nunca GoGroup, Go Group, goGroup.
- **Estados da interface dentro da paleta (não há verde):** ok = azul `#2659a5` com ícone de check; espera = laranja `#f8ae13`
  (texto escuro); crítico = carmim `#e5273c`; informação/agora = ciano `#3dbfef`; destaque de fraude = rosa `#e61782`.
