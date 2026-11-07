# Goxy

**O atendimento que investiga antes de pagar.** Goxy é uma equipe de agentes de IA que assume o atendimento ao cliente da Gocase de ponta a ponta: lê o contato, investiga nos sistemas, decide por uma política auditável, age (troca, reenvio, reembolso, chamado na transportadora) e responde no canal de origem. Só chama uma pessoa quando o caso exige, pelo Slack, Telegram ou painel.

Projeto do **GoHacks Talent 2026** (Gogroup), Desafio 1: Atendimento ao Cliente.

- Em produção: https://goxy.gabrielrichard.dev (landing em `/`, sistema em `/app/`)
- Especificação original: [`SPEC.md`](SPEC.md)
- Arquitetura e decisões: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md)
- Operação, deploy e canais: [`docs/OPERACAO.md`](docs/OPERACAO.md)
- Roteiro de demo e testes manuais: [`docs/DEMO.md`](docs/DEMO.md)
- Contratos de API e eventos: [`goxys/CONTRACTS.md`](goxys/CONTRACTS.md)

## O problema

O atendimento recebe cerca de 4 mil conversas por mês. Cada uma consome uns 12 minutos de uma pessoa, alternando entre pedido, gateway, portal da transportadora e sistema de trocas. Parte das reclamações de avaria não é verdadeira: na base real do desafio, **9 fotos reaproveitadas de outros clientes foram pagas (R$ 527,24)**, uma delas em 6 minutos.

## Como funciona

```
Canais (painel, WhatsApp, Telegram, Slack, Reclame Aqui)
        │
        ▼
API FastAPI + barramento de eventos (SSE para o painel)
        │
        ▼
Maestro em LangGraph ──► Porteiro ─► Rastreador ‖ Detetive ‖ Analista ─► Política A–D
                                                                   │
                         A/B: Executor age sozinho ◄───────────────┤
                         C:   pausa e pede aprovação (Slack/Telegram/painel)
                         D:   passa para uma pessoa e congela as ações
                                                                   │
                                                          Voz responde ao cliente
        │
        ▼
Sistemas (ERP, financeiro, logística, aprovações, Reclame Aqui, portal da transportadora)
        │
        ▼
SQLite: base real do evento + estado dos casos + checkpoints do LangGraph
```

- **A IA classifica e escreve; quem decide se a empresa paga é regra.** A política fica em [`goxys/policy/autonomia.yaml`](goxys/policy/autonomia.yaml), e cada decisão guarda a versão aplicada.
- **Travas no sistema, não no agente:** reembolso acima de R$ 500 sem aprovação recebe 403; a mesma chave de idempotência nunca paga duas vezes.
- **Execução durável:** uma aprovação pendente sobrevive a um reinício do servidor (checkpoint do LangGraph em SQLite).
- **Nunca inventa dado:** sem pedido válido, o Goxy pede o número certo em vez de supor um status.

| Nível | Quando | O que o Goxy faz |
| --- | --- | --- |
| A | Até R$ 150, prova clara, cliente sem alerta | Resolve sozinho |
| B | Até a alçada, prova boa | Resolve e avisa, com "Desfazer" por 15 min |
| C | Acima da alçada, prova duvidosa, irreversível, reação adversa, manipulação | Pausa e pede aprovação |
| D | Ameaça jurídica, imprensa, fraude objetiva | Passa para uma pessoa |

## Rodar localmente

Requisitos: Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20.

```bash
cd goxys
uv venv .venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env                      # preencha AI_PROXY_API_KEY; canais são opcionais
python3 -c "import zipfile; zipfile.ZipFile('../GoHacks_Talent_2026_bases_csv.zip').extractall('../data_raw')"
.venv/bin/python data/build_db.py         # CSVs do evento -> data/goxys.db (~0,5 s)
(cd web && npm ci && npm run build)       # painel React -> web/dist
.venv/bin/uvicorn app:app --port 8000
```

Abra http://localhost:8000. No sistema, use **Simular contato** para disparar os casos de demo ou escrever como cliente escolhendo um pedido real.

Sem `AI_PROXY_API_KEY`, todos os agentes funcionam por regras: o fluxo não para, só perde a redação mais natural. Sem tokens de canal, o canal desliga e o painel continua como plano B.

## Testes

```bash
cd goxys
.venv/bin/python -m pytest -q                                   # 67 testes
.venv/bin/python scripts/clientes_sinteticos.py --auto-aprovar  # personas com pedidos reais; placar em data/placar.json
```

## Deploy

Imagem única com [`Dockerfile`](Dockerfile) e [`docker-compose.yml`](docker-compose.yml), atrás do Caddy. Passo a passo em [`docs/OPERACAO.md`](docs/OPERACAO.md).

## Estrutura

| Caminho | Conteúdo |
| --- | --- |
| `goxys/app.py` | Processo único: sistemas simulados, API, canais e painel |
| `goxys/core/` | Configuração, estado do caso, barramento de eventos, cliente de IA |
| `goxys/agents/` | Maestro em LangGraph (`grafo.py`) e os agentes |
| `goxys/policy/` | Política de autonomia A–D |
| `goxys/tools/` | Ferramentas tipadas que chamam os sistemas por HTTP |
| `goxys/mock_api/` | ERP, financeiro, logística, aprovações, mensagens, Reclame Aqui, portal TransNorte |
| `goxys/channels/` | Telegram, Slack, WhatsApp (Twilio), Reclame Aqui |
| `goxys/web/` | Painel React + Vite + Tailwind |
| `goxys/data/` | Build do banco e casos de demo |
| `goxys/scripts/` | Clientes sintéticos, mascote (ComfyUI), fotos de catálogo |
| `goxys/tests/` | Testes automatizados |
| `assets/` | Mascote, logos, fotos de catálogo |
| `GoHacks_Talent_2026_bases_csv.zip` | Base de dados do evento |

Os dados de clientes e pedidos são sintéticos, fornecidos pelo evento.
