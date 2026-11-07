# Arquitetura

Visão técnica do Goxy: como as peças se conectam e por que cada escolha foi feita. Contratos detalhados (rotas, eventos, formatos) estão em [`goxys/CONTRACTS.md`](../goxys/CONTRACTS.md).

## Visão geral

O Goxy é um **monólito modular**: um processo FastAPI que serve os sistemas simulados, o orquestrador, os canais e o painel. Cada fronteira é um contrato HTTP, então separar em serviços depois é questão de implantação, não de reescrita.

| Camada | O que faz | Onde |
| --- | --- | --- |
| Canais | Entrada e saída: painel, WhatsApp, Telegram, Slack, Reclame Aqui | `goxys/channels/`, `goxys/web/` |
| API e eventos | Rotas `/api/goxys/*` e barramento que alimenta o SSE e os canais | `goxys/api.py`, `goxys/core/bus.py` |
| Orquestração | Maestro em LangGraph, política A–D, IA | `goxys/agents/`, `goxys/policy/`, `goxys/core/llm.py` |
| Sistemas | ERP, financeiro, logística, aprovações, mensagens, Reclame Aqui, portal | `goxys/mock_api/` |
| Dados | SQLite com a base do evento, snapshots dos casos e checkpoints | `goxys/data/` |

## Orquestração (LangGraph)

O Maestro é um `StateGraph` em [`goxys/agents/grafo.py`](../goxys/agents/grafo.py):

```
porteiro ─┬─► rastreador ─────────────┐
          ├─► detetive_prep ─► detetive ┼─► politica ─┬─ A/B ─► executor ─► voz
          └─► analista ────────────────┘              ├─ C ─► revisor ─► pedir_aprovacao ─► aprovacao (interrupt) ─► executor | negar ─► voz
                                                      └─ D ─► revisor ─► humano ─► voz
```

- **Ramos paralelos:** Rastreador, Detetive e Analista rodam juntos e se encontram na Política.
- **Pausa para humano:** o nó de aprovação chama `interrupt()`. Quando chega `approval.decided` (de qualquer canal), o Maestro retoma com `Command(resume=decisao)`.
- **Durabilidade:** `AsyncSqliteSaver` em `data/checkpoints.db`, com `thread_id = case_id`. Casos são recarregados na subida, e um caso pendente sobrevive a um reinício.
- **Entradas extras:** `politica` (quando uma pessoa devolve o caso) e `investigar` (quando o cliente informa o pedido depois).
- O LLM **não escolhe** o próximo passo: o caminho é código e política, para cada decisão ser auditável.

## Estado do caso e eventos

Todos os agentes leem e escrevem num único objeto `Case` ([`goxys/core/state.py`](../goxys/core/state.py)): triagem, dossiê, veracidade, decisão, aprovação, responsável, ações, timeline e uso de IA. Cada mudança grava um snapshot em SQLite (`goxys_cases`).

Cada passo publica um evento no barramento (`agent.started`, `tool.called`, `policy.decided`, `approval.requested`, `approval.decided`, `message.sent`, `case.handoff`…). Consumidores: o painel (SSE em `/api/goxys/stream`), os canais e o próprio Maestro. Um ouvinte com erro nunca derruba o fluxo.

## IA

Toda chamada passa por [`goxys/core/llm.py`](../goxys/core/llm.py), que fala com o AI Proxy do evento (formato OpenAI), registra tokens e custo e bloqueia modelos `deepseek-*`.

| Agente | Chamada | Uso | Sem IA |
| --- | --- | --- | --- |
| Porteiro | Jev (`/v1/systemone`) | Intenção, urgência, riscos, reação adversa, injeção de prompt | Regras e regex |
| Detetive | Chat | Justificativa do score | Template |
| Voz | Chat | Resposta ao cliente e "Sugerir resposta" | Templates |
| Passagem | Chat | Resumo de 3 linhas para quem recebe o caso | Template |
| Detetive (score), Política, Executor | Nenhuma | Score, nível, ações | Sempre regra |

Guarda-corpos: a Voz recebe os fatos prontos e não pode inventar outros; uma reescrita que acrescente ou remova números de pedido/protocolo é descartada; texto do cliente é dado, nunca instrução.

## Política de autonomia

[`goxys/policy/autonomia.yaml`](../goxys/policy/autonomia.yaml), versionada. Sinais: valor do item afetado, score de veracidade (0–100) e risco. A alçada do Goxy é a mesma de um atendente humano (`politicas.csv`: reenvio até R$ 200, reembolso até R$ 100). Na dúvida, escala: mandar ao supervisor custa R$ 6; negar um cliente honesto custa metade do LTV (`parametros.md`).

Ciclo de decisão completo: prazo de aprovação com escalada, ajustar valor, desfazer real no nível B, segregação de funções (ninguém aprova a própria ação) e concordância entre o Goxy e as pessoas. Sugestões de ajuste de limite saem das decisões reais e só são aplicadas depois de passar em 100% dos clientes sintéticos, com confirmação humana.

## Sistemas e travas

Os agentes nunca tocam o banco: usam ferramentas tipadas ([`goxys/tools/`](../goxys/tools/__init__.py)) que chamam APIs HTTP. Trocar pelos sistemas reais é mudar `GOXYS_SYSTEMS_URL` e a autenticação.

Travas no próprio sistema:

1. Reembolso acima de R$ 500 sem aprovação: 403.
2. Mesma `Idempotency-Key` nunca paga duas vezes.
3. Reembolso acima do valor pago: 422.
4. Resposta pública no Reclame Aqui sem aprovação: 403.
5. Transportadora sem API (TransNorte): 501, e o agente faz login no portal e lê o HTML.

## Dados

[`goxys/data/build_db.py`](../goxys/data/build_db.py) carrega os CSVs de `atendimento/` num SQLite. O "agora" do sistema é o da base: 02/11/2026 às 08:00. Os casos de demo vêm de clientes e pedidos reais, exceto o caso 4 (não há pedido acima de R$ 500 entregue recentemente). Fotos de catálogo (site da Gocase) aparecem só como "o que a cliente comprou"; a evidência de avaria é sempre o registro real do anexo (hash, EXIF, descrição visual).

## Decisões principais

| Decisão | Alternativa descartada | Por quê |
| --- | --- | --- |
| LangGraph | Orquestração própria em asyncio | Durabilidade e pausa para humano nativas |
| LangGraph | CrewAI / agente único | Lá o LLM escolhe o próximo passo; aqui decide a política |
| Score por regra | Score pelo LLM | Decide dinheiro: precisa ser reproduzível |
| Jev na triagem | Chat | Mais rápido, barato e com confiança calibrada |
| Monólito modular | Microsserviços | Menos peças para cair; fronteiras já são contratos |
| SQLite | Postgres/Redis | Zero infraestrutura |
| Socket Mode / long polling | Webhooks (Slack/Telegram) | Funcionam sem endereço público |
| Evidência = registro real | Foto gerada | A base não tem imagens; inventar seria desonesto |
