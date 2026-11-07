# Operação

Como configurar, implantar e manter o Goxy.

## Variáveis de ambiente (`goxys/.env`)

Copie [`goxys/.env.example`](../goxys/.env.example) para `goxys/.env`. O `.env` nunca vai para o git.

| Variável | Obrigatória | Para quê |
| --- | --- | --- |
| `AI_PROXY_API_KEY` | Recomendada | Token do AI Proxy do evento (`gw-tok-…`). Sem ele, tudo roda por regras |
| `GOXYS_LLM_MODEL` / `GOXYS_LLM_FAST_MODEL` | Não | Modelos do proxy (padrão `latest` / `glm-5.3-flash`) |
| `GOXYS_PUBLIC_URL` | Em produção | URL pública, ex.: `https://goxy.gabrielrichard.dev` (usada na assinatura do WhatsApp) |
| `TELEGRAM_BOT_TOKEN` | Não | Bot do @BotFather |
| `TELEGRAM_APPROVERS_CHAT_ID` | Não | Chat que recebe os pedidos de aprovação |
| `SLACK_BOT_TOKEN` | Não | `xoxb-…` (OAuth & Permissions) |
| `SLACK_APP_TOKEN` | Não | `xapp-…` com escopo `connections:write` (Socket Mode) |
| `SLACK_APPROVALS_CHANNEL` | Não | ID do canal de aprovações (`C…`) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Não | Conta Twilio; o Auth Token valida a assinatura do webhook |
| `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` | Não | Autenticação REST para enviar mensagens |
| `TWILIO_WHATSAPP_FROM` | Não | Remetente, ex.: `whatsapp:+14155238886` (sandbox) |
| `GOXYS_STEP_DELAY` | Não | Pausa visual entre agentes na demo (s). Em produção: `0` |
| `GOXYS_APPROVAL_SLA_S` | Não | Prazo da aprovação antes de escalar (padrão 1800) |

## Canais

Cada canal liga sozinho quando suas variáveis existem. `/api/goxys/saude` mostra quais estão ativos.

**Uma instância por canal.** O Telegram (long polling) e o Slack (Socket Mode) só funcionam corretamente com **um** processo conectado. Se rodar local e VPS ao mesmo tempo, desligue os canais em um deles:

```bash
TELEGRAM_BOT_TOKEN= SLACK_BOT_TOKEN= SLACK_APP_TOKEN= .venv/bin/uvicorn app:app --port 8000
```

- **Slack:** crie o app a partir do manifesto em [`goxys/channels/README.md`](../goxys/channels/README.md), ative o Socket Mode, gere o App-Level Token (`connections:write`), instale no workspace, convide o bot no canal (`/invite @Goxy`).
- **Telegram:** crie o bot no @BotFather, mande `/start` para ele; o `/start` responde o `chat_id` a usar em `TELEGRAM_APPROVERS_CHAT_ID`.
- **WhatsApp (Twilio sandbox):** no Console, Messaging → Try it out → Send a WhatsApp message. No celular, envie `join <código>` para o número do sandbox. Em Sandbox settings, "When a message comes in" = `https://<seu-domínio>/api/canais/whatsapp` (POST). Requer URL pública e `GOXYS_PUBLIC_URL` igual a ela.

## Deploy (Docker + Caddy)

A imagem compila o painel, instala o backend, extrai a base do evento e monta o banco.

```bash
# na máquina de desenvolvimento
rsync -az --delete --exclude-from=.dockerignore --exclude='.git' ./ <vps>:/opt/goxy/
scp goxys/.env <vps>:/opt/goxy/goxys/.env

# na VPS
cd /opt/goxy && chmod 600 goxys/.env && docker compose up -d --build
```

[`docker-compose.yml`](../docker-compose.yml) usa a rede externa `caddy_web` e limite de 1,2 GB de memória. No Caddy:

```
goxy.gabrielrichard.dev {
    reverse_proxy goxy:8000 {
        flush_interval -1
    }
}
```

`flush_interval -1` mantém o SSE do painel fluindo em tempo real. Depois de editar: `docker exec caddy-proxy caddy reload --config /etc/caddy/Caddyfile`.

Para desligar canais só na VPS sem mexer no `.env`, crie `docker-compose.override.yml`:

```yaml
services:
  goxy:
    environment:
      TELEGRAM_BOT_TOKEN: ""
```

## Manutenção

| Tarefa | Como |
| --- | --- |
| Ver saúde | `curl https://<domínio>/api/goxys/saude` |
| Ver logs | `docker logs -f goxy` |
| Limpar a demo | Painel → Simular contato → Limpar demo, ou `POST /api/goxys/demo/limpar` |
| Reconstruir o banco | `.venv/bin/python data/build_db.py` (apaga casos e tabelas `sim_*`) |
| Atualizar a VPS | Repetir o `rsync` + `docker compose up -d --build` |
| Reiniciar sem perder casos pendentes | `docker compose restart goxy` (checkpoints persistem no container) |

Observação: `docker compose up --build` recria o container e zera o estado dos casos (o banco é gerado na imagem). Para persistir entre rebuilds, monte um volume em `goxys/data`.

## Segurança

- Segredos só no `.env` (permissão 600), nunca no git nem em logs.
- Depois de eventos/demonstrações, **troque os tokens** que circularam em conversas: AI Proxy, Telegram, Slack (bot, app, client secret, signing secret) e Twilio (Auth Token, API Key).
- O webhook do WhatsApp recusa requisições sem assinatura válida (403).
- Ferramentas de escrita registram quem pediu (`X-Agente`) e o caso (`X-Caso`); toda escrita fica em `sim_eventos`.

## Problemas comuns

| Sintoma | Causa provável | Solução |
| --- | --- | --- |
| Telegram `409 Conflict` | Duas instâncias com o mesmo bot | Desligue o canal em uma delas |
| Aprovação no Telegram diz "não existe mais" | A demo foi limpa ou o caso já foi decidido | Dispare o caso de novo |
| Aviso de certificado (emissor Fortinet) | Rede corporativa inspecionando HTTPS | Use outra rede (ex.: 4G) |
| Webhook do WhatsApp retorna 403 | `GOXYS_PUBLIC_URL` diferente da URL configurada na Twilio | Deixe as duas idênticas |
| Painel não atualiza ao vivo | Proxy segurando o SSE | `flush_interval -1` no Caddy |
