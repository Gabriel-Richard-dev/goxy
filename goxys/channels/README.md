# Canais do Goxy

WhatsApp via Twilio (cliente), Telegram (cliente e aprovadores) e Slack (time interno). Todos são opcionais: sem token o canal
fica desligado, o app sobe normalmente e o painel faz o papel de fila de aprovação. As aprovações de
qualquer canal passam pela mesma rota (`POST /api/aprovacoes/{id}/decisao`). Quando alguém decide em
um canal, a mensagem é atualizada nos outros também.

## Variáveis no `goxys/.env`

```env
TELEGRAM_BOT_TOKEN=123456:ABC...        # BotFather
TELEGRAM_APPROVERS_CHAT_ID=-100123...   # grupo dos aprovadores (opcional)
SLACK_BOT_TOKEN=xoxb-...                # OAuth & Permissions → Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...                # Basic Information → App-Level Token (connections:write)
SLACK_APPROVALS_CHANNEL=C0123456789     # id do canal (ou #goxys-aprovacoes)
GOXYS_PUBLIC_URL=http://localhost:8000  # link "abrir no painel"; em produção https://goxy.gabrielrichard.dev
                                        # (o WhatsApp valida a assinatura da Twilio com este URL)
TWILIO_ACCOUNT_SID=AC...                # Console → Account Info
TWILIO_AUTH_TOKEN=...                   # só para validar X-Twilio-Signature dos webhooks
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # número do sandbox
TWILIO_API_KEY_SID=SK...                # (opcional) API Key para a REST; sem ela usa SID + Auth Token
TWILIO_API_KEY_SECRET=...
```

## WhatsApp via Twilio (sandbox, 5 minutos)

O WhatsApp é o canal do **cliente**; aprovações continuam no Slack, no Telegram e no painel.

1. No celular de quem vai testar, mande `join <código-do-sandbox>` para **+1 415 523 8886** (o código aparece no Console).
   Sem isso a Twilio recusa o envio (erro 63015) e o Goxy registra `channel.error`.
2. Twilio Console → **Messaging → Try it out → Send a WhatsApp message → Sandbox settings** →
   **When a message comes in** = `https://goxy.gabrielrichard.dev/api/canais/whatsapp`, método **POST** → Save.
3. Confira `GOXYS_PUBLIC_URL=https://goxy.gabrielrichard.dev` no `.env` do servidor e reinicie: o log mostra
   `canal WhatsApp ligado (Twilio, de whatsapp:+14155238886, REST via API Key; webhook …/api/canais/whatsapp)`.
4. Mande "Oi, meu copo chegou rachado, pedido GCH559588" do celular: o caso abre com `canal: "whatsapp"`, a confirmação
   e a resposta da Voz chegam no WhatsApp. Foto mandada no WhatsApp vira anexo (`/assets-goxys/uploads/wa_…`).

Como funciona: a Twilio faz POST form-urlencoded no webhook; o Goxy valida `X-Twilio-Signature` (HMAC-SHA1 do URL público +
parâmetros, com o Auth Token; inválida → 403), responde `<Response/>` na hora e processa em background. Número sem caso
aberto → caso novo; número com caso aberto (em andamento, esperando o cliente, aprovação ou pessoa) → a mensagem entra
nesse caso, e o número do pedido retoma um caso em `aguardando_cliente`. As respostas saem pela REST
(`Messages.json`) quando há `message.sent` do caso. Erros de envio só geram log + evento `channel.error`.

## Telegram (5 minutos)

1. No Telegram, fale com **@BotFather** e mande `/newbot`. Escolha nome e usuário (ex.: `goxys_demo_bot`) e copie o token para `TELEGRAM_BOT_TOKEN`.
2. (Opcional) Mande `/setprivacy` → **Disable**, para o bot ler as respostas no grupo de aprovadores.
3. Crie um grupo "Goxy aprovações", adicione o bot e mande qualquer mensagem. Abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates`, copie o `chat.id` (negativo) e coloque em `TELEGRAM_APPROVERS_CHAT_ID`.
4. Suba o app. No chat privado com o bot, `/start` explica o que ele faz e `/demo 1` a `/demo 5` dispara um caso de demo
   que responde no próprio chat.

O que acontece:
- O cliente manda texto ou foto. O bot responde na hora "Recebi! Já estou olhando seu pedido." e abre o caso
  (`canal=telegram`). As fotos ficam em `gohacks/assets/uploads/` e são servidas em `/assets-goxys/uploads/`.
  Mensagens mandadas com menos de 2,5 s de intervalo entram no mesmo caso.
- As respostas do Goxy (`message.sent`) voltam para o chat do cliente.
- Os pedidos de aprovação chegam ao grupo com os botões Aprovar, Negar e Ajustar. Em Ajustar, o bot pede o novo
  valor e uma nota, que você manda respondendo à mensagem dele (ex.: `350 reembolso parcial`).

Usa long polling, então não precisa de URL pública.

## Slack (Socket Mode, sem URL pública)

1. Abra https://api.slack.com/apps → **Create New App** → **From a manifest**, escolha o workspace e cole o manifest abaixo.
2. **Basic Information → App-Level Tokens → Generate**, com o escopo `connections:write`. O token `xapp-...` vai em `SLACK_APP_TOKEN`.
3. **Install to Workspace**. O token `xoxb-...` vai em `SLACK_BOT_TOKEN`.
4. Crie o canal `#goxys-aprovacoes` e mande `/invite @Goxy` nele. Pegue o ID do canal (clique no nome do canal, o ID fica
   no rodapé) e coloque em `SLACK_APPROVALS_CHANNEL`.

```yaml
display_information:
  name: Goxy
  description: Equipe de agentes de atendimento — aprovações e avisos
  background_color: "#2659a5"
features:
  bot_user:
    display_name: Goxy
    always_online: true
  slash_commands:
    - command: /goxys
      description: Status, demo e insights do Goxy
      usage_hint: "[status | demo 1..5 | insights]"
      should_escape: false
oauth_config:
  scopes:
    bot:
      - chat:write
      - chat:write.public
      - commands
      - users:read
      - im:write
settings:
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

O que acontece:
- `approval.requested` vira uma mensagem em Block Kit com resumo, evidências, nível, valor e os botões Aprovar, Negar e Ajustar.
  Ajustar abre um modal com valor e nota.
- Depois da decisão, a mensagem é editada com o resultado: quem decidiu, por qual canal e em quanto tempo.
  Se o SLA estoura (`approval.escalated`), a mensagem ganha o aviso de escalonamento e o supervisor é chamado na thread.
- Nas ações de nível B (executadas com aviso), o supervisor recebe o aviso com o botão **Desfazer (15 min)**.
- Comandos: `/goxys` (status), `/goxys demo 3` e `/goxys insights` (clusters do Analista).

## Diagnóstico

Na subida, o log mostra `canal telegram ligado: @bot` ou `canal X desligado (motivo)`. `GET /api/goxys/saude` diz quais canais estão ativos
(`channels.status()`), inclusive `whatsapp`. No WhatsApp: 403 no webhook = `GOXYS_PUBLIC_URL` diferente do URL configurado
na Twilio (ou Auth Token errado); `channel.error` com código 63015 = o número ainda não mandou o `join` do sandbox.
