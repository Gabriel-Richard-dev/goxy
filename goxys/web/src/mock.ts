// Modo mock (?mock=1): simula o backend inteiro no navegador — API e eventos SSE dos 5 casos de demo.
import type { Anexo, BusEvent, Case, Dict, StepStatus, ToolCall } from './types'
import { ApiError } from './errors'

type Listener = (e: BusEvent) => void

const listeners = new Set<Listener>()
const historico: BusEvent[] = []
const casos: Record<string, Case> = {}
const aprovacoes: Record<string, Dict> = {}
const esperas: Record<string, (d: Dict) => void> = {}
let seq = 1
let casoSeq = 1
let acaoSeq = 1

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const agora = () => new Date().toISOString().slice(0, 23)
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x))

function emit(type: string, data: Dict, case_id: string | null = null, agent: string | null = null) {
  const e: BusEvent = { id: seq++, type, ts: agora(), case_id, agent, data }
  historico.push(e)
  listeners.forEach((l) => l(e))
}

function salvar(c: Case, ev = 'case.updated') {
  c.atualizado_em = agora()
  emit(ev, { case: clone(c) }, c.id)
}

function novoCaso(f: Partial<Case>): Case {
  const id = `GX${String(new Date().getHours()).padStart(2, '0')}${String(new Date().getMinutes()).padStart(2, '0')}${String(casoSeq++).padStart(3, '0')}`
  const c: Case = {
    id, canal: 'whatsapp', mensagem: '', mensagens: [], anexos: [], status: 'novo', mascot_state: 'parado',
    criado_em: agora(), atualizado_em: agora(), plano: [], triagem: {}, dossie: {}, veracidade: {}, decisao: {},
    aprovacao: {}, resposta: {}, acoes: [], timeline: [], llm_uso: { chamadas: 0, tokens_in: 0, tokens_out: 0, usd: 0 },
    ...f,
  }
  c.mensagens = [{ autor: 'cliente', nome: c.cliente_nome ?? undefined, texto: c.mensagem, ts: c.criado_em }]
  casos[id] = c
  salvar(c, 'case.created')
  return c
}

async function mascote(c: Case, state: Case['mascot_state']) {
  c.mascot_state = state
  salvar(c)
  emit('mascot.state', { state }, c.id)
}

async function passo(
  c: Case, agent: string, rodando: string, ms: number,
  fim: { status?: StepStatus; titulo: string; detalhe?: string; evidencias?: Dict[] },
  durante?: () => Promise<void> | void,
) {
  const s = c.timeline.find((x) => x.agent === agent)
  if (s) Object.assign(s, { status: 'rodando', titulo: rodando, inicio: agora() })
  else c.timeline.push({ agent, status: 'rodando', titulo: rodando, detalhe: '', evidencias: [], inicio: agora() })
  emit('agent.started', { titulo: rodando }, c.id, agent)
  salvar(c)
  const t0 = Date.now()
  await sleep(ms * 0.5)
  if (durante) await durante()
  await sleep(ms * 0.5)
  const st = c.timeline.find((x) => x.agent === agent)!
  Object.assign(st, { status: fim.status ?? 'ok', titulo: fim.titulo, detalhe: fim.detalhe ?? '', evidencias: fim.evidencias ?? [], fim: agora(), duracao_ms: Date.now() - t0 })
  emit('agent.finished', { status: st.status, titulo: st.titulo, detalhe: st.detalhe, evidencias: st.evidencias, duracao_ms: st.duracao_ms }, c.id, agent)
  salvar(c)
}

async function ferramenta(c: Case, agent: string, nome: string, tipo: ToolCall['tipo'], parametros: Dict, resultado: unknown, status_http = 200, ms = 180) {
  await sleep(ms)
  const tc: ToolCall = { acao_id: `GXA${String(acaoSeq++).padStart(6, '0')}`, agent, ferramenta: nome, tipo, parametros, resultado, status_http, nivel_autonomia: c.decisao?.nivel ?? null, ts: agora(), duracao_ms: ms }
  c.acoes.push(tc)
  emit('tool.called', { ferramenta: nome, tipo, parametros, status_http, resultado_resumo: typeof resultado === 'object' ? JSON.stringify(resultado).slice(0, 140) : String(resultado), duracao_ms: ms }, c.id, agent)
}

function llm(c: Case, agent: string, kind: string, tin: number, tout: number) {
  const usd = +(tin * 0.00000015 + tout * 0.0000006).toFixed(5)
  c.llm_uso = { chamadas: c.llm_uso.chamadas + 1, tokens_in: c.llm_uso.tokens_in + tin, tokens_out: c.llm_uso.tokens_out + tout, usd: +(c.llm_uso.usd + usd).toFixed(5) }
  emit('llm.call', { kind, model: kind === 'jev' ? 'jev-classify' : 'gpt-4.1-mini', tokens_in: tin, tokens_out: tout, usd, ms: 420 }, c.id, agent)
}

async function politica(c: Case, nivel: string, acao: string, valor: number, motivo: string, sinais: string[]) {
  await sleep(300)
  c.decisao = { acao, valor, nivel, motivo, porque: motivo.split(':')[0].replace(/\.$/, '') + '.', sinais, politica_versao: 'autonomia.yaml v3 (2026-11-01)', revisor: nivel >= 'C' ? 'maker-checker: Revisor concordou' : null }
  if (nivel === 'B') c.decisao.desfazer_ate = new Date(Date.now() + 15 * 60000).toISOString()
  if (nivel === 'C') c.sugestao_responsavel = { ...EQUIPE[0], motivo: 'o valor passa do limite de quem atende' }
  if (nivel === 'D') c.sugestao_responsavel = { ...EQUIPE[2], motivo: c.triagem?.risco_juridico ? 'há ameaça jurídica' : 'a prova é falsa e precisa de revisão' }
  emit('policy.decided', { nivel, acao, valor, motivo, sinais }, c.id, 'maestro')
  salvar(c)
}

async function responder(c: Case, texto: string) {
  await passo(c, 'voz', 'escrevendo a resposta no tom da casa', 1400, { titulo: `respondeu no ${nomeCanal(c.canal)}` }, async () => {
    llm(c, 'voz', 'chat', 820, 140)
    await ferramenta(c, 'voz', 'send_message', 'escrita', { canal: c.canal, case_id: c.id }, { message_id: `MSG${c.id}` })
    c.resposta = { texto, canal: c.canal, message_id: `MSG${c.id}` }
    c.mensagens.push({ autor: 'goxys', texto, ts: agora() })
    emit('message.sent', { canal: c.canal, texto, message_id: `MSG${c.id}`, customer_id: c.customer_id }, c.id, 'voz')
  })
}

async function encerrar(c: Case, status: Case['status'], m: Case['mascot_state'], resumo: string) {
  c.status = status
  c.encerrado_em = agora()
  await mascote(c, m)
  emit('case.closed', { status, resumo }, c.id)
}

const nomeCanal = (c: string) => ({ whatsapp: 'WhatsApp', email: 'e-mail', telegram: 'Telegram', reclame_aqui: 'Reclame Aqui', painel: 'painel' } as Dict)[c] ?? c

async function inicio(c: Case, plano: string[]) {
  c.status = 'em_andamento'
  c.plano = plano.map((agent, i) => ({ agent, depende_de: i ? [plano[i - 1]] : [] }))
  c.timeline = ['maestro', ...plano].map((agent) => ({ agent, status: 'pendente', titulo: '', detalhe: '', evidencias: [] }))
  await mascote(c, 'investigando')
  await passo(c, 'maestro', 'montando o plano do caso', 700, { titulo: `planejou ${plano.length} passos`, detalhe: plano.join(' → ') })
}

// ---------- os 5 casos ----------

const fotoInedita: Anexo = { anexo_id: 'DEMO-IMG01', tipo: 'foto_produto', hash_perceptual: 'c3a1f09e7b42d815', exif_data_captura: '2026-10-31T19:42:10', exif_modelo_camera: 'iPhone 13', exif_software: '', descricao_visual: 'copo térmico rosa com a tampa rachada na borda', enviado_em: '2026-11-02T07:51:00' }

async function caso1() {
  const c = novoCaso({ demo_caso: 1, canal: 'whatsapp', customer_id: 'CH004812', order_id: 'GCH58213', conversation_id: 'DEMO-AT001', cliente_nome: 'Marina Albuquerque', mensagem: 'Oi! Meu copo chegou com a tampa rachada, mandei a foto. Pedido GCH58213. Dá pra resolver?', anexos: [fotoInedita] })
  await inicio(c, ['porteiro', 'rastreador', 'detetive', 'executor', 'voz'])
  await passo(c, 'porteiro', 'lendo a mensagem', 1000, { titulo: 'classificou como avaria', detalhe: 'Intenção avaria, urgência média, sentimento neutro.' }, () => {
    llm(c, 'porteiro', 'jev', 310, 24)
    c.triagem = { intencao: 'avaria', urgencia: 'media', sentimento: 'neutro', idioma: 'pt', risco_juridico: false, imprensa: false, confianca: 0.94 }
  })
  await passo(c, 'rastreador', 'buscando pedido e entrega', 1500, { titulo: 'pedido entregue em 31/10', evidencias: [{ tipo: 'pedido', order_id: 'GCH58213', valor: 89.9 }] }, async () => {
    await ferramenta(c, 'rastreador', 'get_order', 'leitura', { order_id: 'GCH58213' }, { valor_total: 89.9, status: 'entregue' })
    await ferramenta(c, 'rastreador', 'get_tracking', 'leitura', { order_id: 'GCH58213' }, { transportadora: 'Loggi', status: 'entregue' })
    await ferramenta(c, 'rastreador', 'get_customer_history', 'leitura', { customer_id: 'CH004812' }, { pedidos: 6, trocas: 0, estornos: 0 })
    c.dossie = { pedido: { order_id: 'GCH58213', valor_total: 89.9, produto: 'Copo térmico Stanley-like 473 ml', sku: 'COPO-473-RS' }, cliente: { nome: 'Marina Albuquerque', flag_risco: false, qtd_reclamacoes_90d: 0 }, rastreio: { transportadora: 'Loggi', status: 'entregue', entregue_em: '2026-10-31' }, fonte_rastreio: 'api', inconsistencias: [] }
  })
  await passo(c, 'detetive', 'examinando a foto', 1800, { titulo: 'foto inédita, avaria confirmada', detalhe: 'Hash nunca visto; EXIF de 31/10 19:42, depois da entrega.', evidencias: [{ tipo: 'exif', data_captura: '2026-10-31T19:42', antes_da_entrega: false }, { tipo: 'reuso', encontrados: 0 }] }, async () => {
    await ferramenta(c, 'detetive', 'analyze_damage_photo', 'leitura', { anexo_id: 'DEMO-IMG01' }, { avaria_detectada: true, confianca: 0.91, reuso: [] }, 200, 600)
    llm(c, 'detetive', 'chat', 1200, 180)
    c.veracidade = { score: 92, faixa: 'procede', evidencias: ['Foto inédita na base', 'EXIF depois da entrega', 'Cliente sem histórico de reclamação'] }
  })
  await politica(c, 'A', 'reenvio', 89.9, 'Avaria comprovada, valor até R$ 150 e cliente sem risco.', ['valor ≤ 150', 'score 92', 'cliente limpo'])
  await passo(c, 'executor', 'gerando o reenvio', 1200, { titulo: 'reenvio RP-77120 gerado', evidencias: [{ tipo: 'reenvio', solicitacao_id: 'RP-77120', custo_total: 41.3 }] }, async () => {
    await ferramenta(c, 'executor', 'create_exchange', 'escrita', { order_id: 'GCH58213', sku: 'COPO-473-RS', tipo: 'reenvio' }, { solicitacao_id: 'RP-77120', pedido_reposicao_id: 'GCH58213-R1', custo_total: 41.3, status: 'aberta' })
  })
  await responder(c, 'Oi, Marina! Vi a foto da tampa rachada e já resolvi: um copo novo sai pra você amanhã, sem custo. O código de rastreio chega por aqui. Não precisa devolver o outro.')
  await encerrar(c, 'resolvido', 'resolvido', 'Reenvio automático, nível A')
}

async function caso2() {
  const atual: Anexo = { anexo_id: 'DEMO-IMG02', tipo: 'foto_produto', hash_perceptual: 'a91f3c07d2e4b658', exif_data_captura: '', exif_modelo_camera: '', exif_software: 'Adobe Firefly 3', descricao_visual: 'capinha com rasgo no canto inferior e risco na lateral', enviado_em: '2026-11-02T07:58:00' }
  const original: Anexo = { anexo_id: 'IMG000123', tipo: 'foto_produto', hash_perceptual: 'a91f3c07d2e4b658', exif_data_captura: '2026-07-30T22:45:00', exif_modelo_camera: 'Galaxy S23', exif_software: '', descricao_visual: 'Capinha de celular transparente com estampa rachada na lateral, perto dos botões.', enviado_em: '2026-09-08T12:36:44', order_id: 'GCH535078', customer_id: 'CH999406' }
  const c = novoCaso({ demo_caso: 2, canal: 'email', customer_id: 'CH009315', order_id: 'GCH60477', conversation_id: 'DEMO-AT002', cliente_nome: 'Rafael Monteiro', mensagem: 'Minha capinha veio rasgada no canto, olha a foto. Quero o dinheiro de volta ou outra nova, pedido GCH60477.', anexos: [atual] })
  await inicio(c, ['porteiro', 'rastreador', 'detetive', 'voz'])
  await passo(c, 'porteiro', 'lendo a mensagem', 900, { titulo: 'classificou como avaria', detalhe: 'Pede reembolso ou troca.' }, () => {
    llm(c, 'porteiro', 'jev', 290, 22)
    c.triagem = { intencao: 'avaria', urgencia: 'media', sentimento: 'irritado', idioma: 'pt', risco_juridico: false, imprensa: false, confianca: 0.9 }
  })
  await passo(c, 'rastreador', 'buscando pedido e histórico', 1300, { titulo: 'pedido entregue em 28/10' }, async () => {
    await ferramenta(c, 'rastreador', 'get_order', 'leitura', { order_id: 'GCH60477' }, { valor_total: 129.9 })
    await ferramenta(c, 'rastreador', 'get_customer_history', 'leitura', { customer_id: 'CH009315' }, { pedidos: 3, trocas: 1 })
    c.dossie = { pedido: { order_id: 'GCH60477', valor_total: 129.9, produto: 'Capinha iPhone 15 Glitter' }, cliente: { nome: 'Rafael Monteiro', flag_risco: false, qtd_reclamacoes_90d: 1 }, rastreio: { transportadora: 'Jadlog', status: 'entregue' }, fonte_rastreio: 'api' }
  })
  await passo(c, 'detetive', 'examinando a foto', 2600, { status: 'alerta', titulo: 'foto reaproveitada de outro pedido', detalhe: 'Mesmo hash perceptual de uma foto enviada em agosto, em outro pedido, por outro cliente. O rasgo foi adicionado por editor de IA.', evidencias: [{ tipo: 'reuso', anexo_id: 'AN-2208431', order_id: 'GCH41187' }, { tipo: 'exif', software: 'Adobe Firefly 3' }] }, async () => {
    await ferramenta(c, 'detetive', 'analyze_damage_photo', 'leitura', { anexo_id: 'DEMO-IMG02' }, { avaria_detectada: true, confianca: 0.42, reuso: [{ anexo_id: 'AN-2208431', order_id: 'GCH41187' }] }, 200, 700)
    llm(c, 'detetive', 'chat', 1650, 260)
    await mascote(c, 'fraude')
    c.veracidade = {
      score: 8, faixa: 'nao_procede',
      evidencias: ['Hash perceptual idêntico ao anexo AN-2208431 (pedido GCH41187, 14/08)', 'EXIF apagado e software Adobe Firefly 3', 'Rasgo no canto não existe na foto original', 'Bordas do rasgo com textura repetida típica de preenchimento generativo'],
      comparacao: {
        original, atual, similaridade: 0.97,
        outros_usos: [{ anexo_id: 'IMG000123', order_id: 'GCH535078', customer_id: 'CH999406', enviado_em: '2026-09-08T12:36:44' }, { anexo_id: 'IMG000267', order_id: 'GCH551285', customer_id: 'CH585887', enviado_em: '2026-10-17T16:39:59' }],
        diferencas: ['rasgo adicionado no canto inferior', 'EXIF removido', 'software de edição por IA'],
        indicios_ia: ['textura repetida na borda do rasgo', 'sombra inconsistente com a luz da cena', 'metadados de câmera ausentes'],
      },
    }
  })
  await politica(c, 'B', 'rejeitar', 0, 'Prova reaproveitada e editada por IA. Recusa com convite a mandar nova foto.', ['hash repetido', 'software de IA', 'score 8'])
  await responder(c, 'Oi, Rafael. Analisamos a foto e ela já tinha sido usada em outro pedido, com uma edição no canto. Por isso não conseguimos seguir com a troca agora. Se o produto tiver mesmo um defeito, mande uma foto nova tirada hoje que a gente olha de novo.')
  await encerrar(c, 'rejeitado', 'fraude', 'Fraude barrada: foto reaproveitada')
}

async function caso3() {
  const c = novoCaso({ demo_caso: 3, canal: 'telegram', customer_id: 'CH011204', order_id: 'GCH59930', conversation_id: 'DEMO-AT003', cliente_nome: 'Juliana Freitas', mensagem: 'Era pra minha garrafa ter chegado dia 28 e até agora nada. Cadê meu pedido GCH59930?' })
  await inicio(c, ['porteiro', 'rastreador', 'executor', 'voz'])
  await passo(c, 'porteiro', 'lendo a mensagem', 900, { titulo: 'classificou como atraso' }, () => {
    llm(c, 'porteiro', 'jev', 260, 20)
    c.triagem = { intencao: 'atraso', urgencia: 'alta', sentimento: 'impaciente', idioma: 'pt', risco_juridico: false, imprensa: false, confianca: 0.96 }
  })
  await passo(c, 'rastreador', 'consultando a TransNorte', 3200, { titulo: 'API indisponível → entrou no portal da TransNorte', detalhe: 'A TransNorte não tem API de rastreio (HTTP 501). O Rastreador fez login no portal e leu a tabela de eventos: parado no CD de Teresina há 5 dias.', evidencias: [{ tipo: 'portal', url: '/portal/rastreio?pedido=GCH59930', previsao: '2026-11-06' }] }, async () => {
    await ferramenta(c, 'rastreador', 'get_order', 'leitura', { order_id: 'GCH59930' }, { valor_total: 119.9, status: 'em_transito' })
    await ferramenta(c, 'rastreador', 'get_tracking', 'leitura', { order_id: 'GCH59930' }, { erro: 'sem_api', portal: '/portal', mensagem: 'TransNorte não tem API de rastreio' }, 501)
    await ferramenta(c, 'rastreador', 'portal_tracking', 'leitura', { order_id: 'GCH59930', portal: '/portal', usuario: 'goxys' }, { status: 'parado no CD', local: 'Teresina/PI', previsao: '2026-11-06', eventos: 4 }, 200, 1400)
    c.dossie = { pedido: { order_id: 'GCH59930', valor_total: 119.9, produto: 'Garrafa térmica 750 ml' }, cliente: { nome: 'Juliana Freitas', flag_risco: false }, rastreio: { transportadora: 'TransNorte', codigo_rastreio: 'TN8821930BR', status: 'parado no CD', data_prometida: '2026-10-28', atrasado: true, dias_atraso: 5, eventos: [{ data_hora: '2026-10-27 18:10', status: 'Saiu do CD Fortaleza', local: 'Fortaleza/CE' }, { data_hora: '2026-10-28 09:40', status: 'Chegou ao CD', local: 'Teresina/PI' }] }, fonte_rastreio: 'portal', inconsistencias: ['ERP diz em trânsito; portal diz parado há 5 dias'] }
  })
  await politica(c, 'A', 'chamado_transportadora', 0, 'Atraso causado pela transportadora: abrir chamado e avisar o cliente.', ['atraso 5 dias', 'sem custo'])
  await passo(c, 'executor', 'abrindo chamado na TransNorte', 1100, { titulo: 'chamado TN-44190 aberto', evidencias: [{ tipo: 'chamado', protocolo: 'TN-44190', prazo_resposta: '24 h' }] }, async () => {
    await ferramenta(c, 'executor', 'open_carrier_ticket', 'escrita', { order_id: 'GCH59930', motivo: 'atraso 5 dias, parado no CD' }, { chamado_id: 'CT-0091', protocolo: 'TN-44190', transportadora: 'TransNorte', prazo_resposta: '24 h' })
  })
  await responder(c, 'Juliana, achei sua garrafa: ela está parada no centro de distribuição de Teresina desde 28/10. Já abri um chamado com a TransNorte (protocolo TN-44190) e eles têm 24 h pra responder. A nova previsão é 06/11 e eu te aviso aqui assim que ela andar.')
  await encerrar(c, 'resolvido', 'resolvido', 'Chamado na transportadora + aviso proativo')
}

async function caso4() {
  const foto: Anexo = { anexo_id: 'DEMO-IMG04', tipo: 'foto_produto', hash_perceptual: '5e0d77a1c9b2f340', exif_data_captura: '', exif_modelo_camera: '', exif_software: '', descricao_visual: 'mala de bordo com marca na lateral; não dá pra ver se é amassado ou sujeira', enviado_em: '2026-11-02T08:03:00' }
  const c = novoCaso({ demo_caso: 4, canal: 'whatsapp', customer_id: 'CH007731', order_id: 'GCH60112', conversation_id: 'DEMO-AT004', cliente_nome: 'Carlos Eduardo Lima', mensagem: 'A mala chegou com a lateral amassada. Quero o reembolso total de R$ 689. Segue foto.', anexos: [foto] })
  await inicio(c, ['porteiro', 'rastreador', 'detetive', 'mensageiro', 'executor', 'voz'])
  await passo(c, 'porteiro', 'lendo a mensagem', 900, { titulo: 'classificou como avaria com pedido de reembolso' }, () => {
    llm(c, 'porteiro', 'jev', 300, 24)
    c.triagem = { intencao: 'reembolso', urgencia: 'media', sentimento: 'neutro', idioma: 'pt', risco_juridico: false, imprensa: false, confianca: 0.88 }
  })
  await passo(c, 'rastreador', 'buscando pedido e pagamento', 1300, { titulo: 'pedido de R$ 689 entregue em 30/10' }, async () => {
    await ferramenta(c, 'rastreador', 'get_order', 'leitura', { order_id: 'GCH60112' }, { valor_total: 689 })
    await ferramenta(c, 'rastreador', 'get_customer', 'leitura', { customer_id: 'CH007731' }, { flag_risco: false, qtd_reclamacoes_90d: 2 })
    c.dossie = { pedido: { order_id: 'GCH60112', valor_total: 689, produto: 'Mala de bordo Gocase Travel' }, cliente: { nome: 'Carlos Eduardo Lima', flag_risco: false, qtd_reclamacoes_90d: 2 }, rastreio: { transportadora: 'Correios', status: 'entregue' }, fonte_rastreio: 'api' }
  })
  await passo(c, 'detetive', 'examinando a foto', 2000, { status: 'alerta', titulo: 'foto duvidosa, score 55', detalhe: 'EXIF ausente e descrição ambígua: amassado ou sujeira. Faixa cinzenta.', evidencias: [{ tipo: 'exif', presente: false }, { tipo: 'score', valor: 55 }] }, async () => {
    await ferramenta(c, 'detetive', 'analyze_damage_photo', 'leitura', { anexo_id: 'DEMO-IMG04' }, { avaria_detectada: true, confianca: 0.55, reuso: [] }, 200, 600)
    llm(c, 'detetive', 'chat', 1300, 210)
    c.veracidade = { score: 55, faixa: 'cinzenta', evidencias: ['EXIF ausente', 'Imagem ambígua: amassado ou sujeira', '2 reclamações em 90 dias'] }
  })
  await politica(c, 'C', 'reembolso', 689, 'Reembolso acima de R$ 500 com prova em faixa cinzenta: pede aprovação humana.', ['valor > 500', 'score 55', 'EXIF ausente'])
  const apId = `AP${c.id.slice(2)}`
  let decisao: Dict = {}
  await passo(c, 'mensageiro', 'montando o pedido de aprovação', 900, { titulo: 'pediu aprovação no Slack, Telegram e painel', status: 'ok' }, async () => {
    await ferramenta(c, 'mensageiro', 'request_approval', 'humano', { valor: 689, nivel: 'C' }, { aprovacao_id: apId, status: 'pendente' })
    const ap = { aprovacao_id: apId, case_id: c.id, titulo: 'Reembolso de R$ 689 com foto duvidosa', resumo: 'Carlos pede reembolso total da mala. Foto sem EXIF e ambígua (score 55). Cliente sem flag de risco, 2 reclamações em 90 dias.', nivel: 'C', valor: 689, acao_proposta: 'reembolso', evidencias: ['Score 55 (faixa cinzenta)', 'EXIF ausente', 'Pedido entregue em 30/10'], expira_em: new Date(Date.now() + 15 * 60000).toISOString(), status: 'pendente', criado_em: agora() }
    aprovacoes[apId] = ap
    c.aprovacao = { aprovacao_id: apId, status: 'pendente' }
    emit('approval.requested', ap, c.id, 'mensageiro')
  })
  c.status = 'aguardando_aprovacao'
  await mascote(c, 'esperando')
  const t0 = Date.now()
  decisao = await new Promise<Dict>((resolve) => {
    esperas[apId] = resolve
    // se ninguém decidir no painel, o "supervisor" decide no Slack
    setTimeout(() => { if (esperas[apId]) decidir(apId, { decisao: 'ajustar', aprovador: 'Ana Paula (supervisora)', canal: 'slack', ajuste: { valor: 350, nota: 'Reembolso parcial: imagem não comprova amassado.' } }) }, 45000)
  })
  c.aprovacao = { ...c.aprovacao, status: 'decidida', decisao: decisao.decisao, aprovador: decisao.aprovador, canal: decisao.canal, tempo_resposta_s: Math.round((Date.now() - t0) / 1000) }
  c.status = 'em_andamento'
  await mascote(c, 'investigando')
  if (decisao.decisao === 'negar') {
    await responder(c, 'Carlos, nosso time analisou a foto e não conseguiu confirmar o amassado. Mande uma foto nova, com a lateral contra a luz, que a gente revê na hora.')
    return encerrar(c, 'resolvido', 'resolvido', 'Reembolso negado por humano')
  }
  const valor = decisao.decisao === 'ajustar' && decisao.ajuste?.valor ? Number(decisao.ajuste.valor) : 689
  await passo(c, 'executor', 'emitindo o reembolso', 1200, { titulo: `reembolso de R$ ${valor.toFixed(2).replace('.', ',')} emitido`, evidencias: [{ tipo: 'reembolso', reembolso_id: 'RB-30871', idempotency_key: `${c.id}-reembolso` }] }, async () => {
    await ferramenta(c, 'executor', 'issue_refund', 'escrita_critica', { order_id: 'GCH60112', valor, aprovacao_id: apId }, { reembolso_id: 'RB-30871', valor, status: 'emitido' })
  })
  await responder(c, `Carlos, aprovamos um reembolso de R$ ${valor.toFixed(2).replace('.', ',')} no mesmo cartão da compra. Ele aparece na fatura em até 2 ciclos.`)
  await encerrar(c, 'resolvido', 'resolvido', 'Reembolso após aprovação humana')
}

async function caso5() {
  const c = novoCaso({ demo_caso: 5, canal: 'reclame_aqui', customer_id: 'CH003390', order_id: 'GCH57754', conversation_id: 'DEMO-AT005', cliente_nome: 'Patrícia Nogueira', mensagem: 'Terceira vez que reclamo e ninguém resolve. Já postei no Reclame Aqui e vou entrar no Procon e com processo se não devolverem meu dinheiro até sexta.' })
  await inicio(c, ['porteiro', 'mensageiro', 'voz'])
  await passo(c, 'porteiro', 'lendo a mensagem', 1100, { status: 'alerta', titulo: 'detectou ameaça jurídica e caso público', detalhe: 'Cita processo, Procon e Reclame Aqui. Sentimento muito negativo.', evidencias: [{ tipo: 'gatilho', termos: ['processo', 'Procon', 'Reclame Aqui'] }] }, () => {
    llm(c, 'porteiro', 'jev', 340, 30)
    c.triagem = { intencao: 'reembolso', urgencia: 'critica', sentimento: 'muito_negativo', idioma: 'pt', risco_juridico: true, imprensa: true, confianca: 0.97 }
  })
  await politica(c, 'D', 'humano_assume', 0, 'Ameaça jurídica e exposição pública: humano assume, ações automáticas congeladas.', ['risco jurídico', 'Reclame Aqui', '3º contato'])
  await passo(c, 'mensageiro', 'chamando a supervisão', 900, { titulo: 'passou o caso para a supervisora', detalhe: 'Dossiê enviado no Slack com histórico dos 3 contatos.' }, async () => {
    await ferramenta(c, 'mensageiro', 'open_internal_ticket', 'humano', { prioridade: 'critica' }, { chamado_id: 'CHM-5521' })
  })
  await responder(c, 'Patrícia, entendo a frustração com três contatos sem solução. Uma pessoa da nossa supervisão já está com o seu caso e fala com você hoje até as 12 h.')
  await encerrar(c, 'humano', 'humano', 'Humano assumiu (nível D)')
}

async function casoLivre(c: Case) {
  await inicio(c, ['porteiro', 'rastreador', 'voz'])
  const t = c.mensagem.toLowerCase()
  const intencao = /atras|cad[eê]|chegou n|n[aã]o chegou/.test(t) ? 'atraso' : /quebr|rach|amass|avari|defeit/.test(t) ? 'avaria' : /processo|procon/.test(t) ? 'reclamacao' : 'duvida'
  await passo(c, 'porteiro', 'lendo a mensagem', 900, { titulo: `classificou como ${intencao}` }, () => {
    llm(c, 'porteiro', 'jev', 280, 22)
    c.triagem = { intencao, urgencia: 'media', sentimento: 'neutro', idioma: 'pt', confianca: 0.8 }
  })
  await passo(c, 'rastreador', 'buscando o pedido', 1100, { titulo: c.order_id ? `pedido ${c.order_id} localizado` : 'sem pedido informado', status: c.order_id ? 'ok' : 'alerta' })
  await responder(c, c.order_id ? 'Recebi sua mensagem e já estou olhando o pedido. Te respondo por aqui em instantes.' : 'Recebi sua mensagem! Me passa o número do pedido (começa com GCH) pra eu verificar.')
  await encerrar(c, 'resolvido', 'resolvido', 'Resposta automática')
}

const roteiros = [caso1, caso2, caso3, caso4, caso5]

const PEDIDOS = [
  { order_id: 'GCH558213', customer_id: 'CH004812', cliente_nome: 'Marina Albuquerque', produto: 'Copo Térmico Vibe 473 ml', sku: 'GOC-6001', valor: 111.0, status_entrega: 'entregue', data_pedido: '2026-10-24' },
  { order_id: 'GCH551285', customer_id: 'CH585887', cliente_nome: 'Joana Araújo', produto: 'Case Anti Impacto Slim Air Xadrez Vichy', sku: 'GOC-5004', valor: 97.8, status_entrega: 'entregue', data_pedido: '2026-10-08' },
  { order_id: 'GCH558661', customer_id: 'CH011204', cliente_nome: 'Tainá Rocha', produto: 'Garrafa Mini 350 ml Lilás', sku: 'GOC-7003', valor: 89.9, status_entrega: 'em_transito', data_pedido: '2026-10-20' },
  { order_id: 'GBH990004', customer_id: 'CH007731', cliente_nome: 'Camila Carvalho', produto: 'Kit 3 perfumes Lescent', sku: 'GBH-1004', valor: 689.7, status_entrega: 'entregue', data_pedido: '2026-10-25' },
]
const JOBS: Record<string, { id: string; t0: number }> = {}
const APRENDIZADO = {
  total_decisoes: 38, concordancia_pct: 92, por_nivel: { A: 100, B: 95, C: 83, D: 100 },
  divergencias: [
    { case_id: 'GX0001', recomendado: 'reembolso', decidido: 'ajustar', aprovador: 'Ana Paula', nota: 'Reembolso parcial: a foto não mostra o dano.' },
    { case_id: 'GX0002', recomendado: 'negar', decidido: 'aprovar', aprovador: 'Marcos', nota: 'Cliente antigo, vale o risco.' },
  ],
  sugestoes: [
    { id: 'S1', regra: 'limite do nível B', de: 500, para: 600, evidencia: '7 aprovações seguidas entre R$ 500 e R$ 600, todas aprovadas sem ajuste.', status: 'proposta' as string, placar: null as Dict | null },
    { id: 'S2', regra: 'Pedir nova foto antes de negar quando a foto não tem EXIF', evidencia: '3 negativas revertidas pelo time em casos sem EXIF.', status: 'proposta' as string, placar: null as Dict | null },
  ],
}

const EQUIPE = [
  { colaborador_id: 'supervisor_01', nome: 'Ana Paula Ribeiro', cargo: 'supervisor', nivel: 'pleno', canais_atendidos: 'whatsapp,email', supervisor_id: null, status: 'ativo' },
  { colaborador_id: 'supervisor_02', nome: 'Marcos Lima', cargo: 'supervisor', nivel: 'senior', canais_atendidos: 'reclame_aqui', supervisor_id: null, status: 'ativo' },
  { colaborador_id: 'analista_01', nome: 'Beatriz Sales', cargo: 'analista', nivel: 'senior', canais_atendidos: 'reclame_aqui,email', supervisor_id: 'supervisor_02', status: 'ativo' },
  { colaborador_id: 'atendente_01', nome: 'João Pedro Alves', cargo: 'atendente', nivel: 'junior', canais_atendidos: 'whatsapp', supervisor_id: 'supervisor_01', status: 'ativo' },
  { colaborador_id: 'atendente_02', nome: 'Larissa Moura', cargo: 'atendente', nivel: 'pleno', canais_atendidos: 'whatsapp,telegram', supervisor_id: 'supervisor_01', status: 'ativo' },
  { colaborador_id: 'atendente_03', nome: 'Rafael Castro', cargo: 'atendente', nivel: 'pleno', canais_atendidos: 'email', supervisor_id: 'supervisor_01', status: 'pausa' },
]

function decidir(id: string, body: Dict) {
  const ap = aprovacoes[id]
  if (!ap) throw { status: 404, body: { erro: 'nao_encontrada' } }
  if (ap.status !== 'pendente') throw { status: 409, body: { erro: 'ja_decidida', mensagem: 'Essa aprovação já foi decidida.' } }
  const tempo = Math.round((Date.now() - new Date(ap.criado_em).getTime()) / 1000)
  Object.assign(ap, { status: 'decidida', decisao: body.decisao, aprovador: body.aprovador, canal: body.canal, ajuste: body.ajuste ?? null })
  emit('approval.decided', { aprovacao_id: id, decisao: body.decisao, aprovador: body.aprovador, canal: body.canal, ajuste: body.ajuste ?? null, tempo_resposta_s: tempo }, ap.case_id, 'mensageiro')
  const w = esperas[id]
  delete esperas[id]
  w?.(body)
  return ap
}

// ---------- API mock ----------

function metricas() {
  const cs = Object.values(casos)
  const fim = cs.filter((c) => c.encerrado_em)
  const auto = fim.filter((c) => ['A', 'B'].includes(c.decisao?.nivel) || (!c.decisao?.nivel && c.status === 'resolvido'))
  const tempos = fim.map((c) => (new Date(c.encerrado_em!).getTime() - new Date(c.criado_em).getTime()) / 1000)
  const porNivel: Dict = { A: 0, B: 0, C: 0, D: 0 }
  const porStatus: Dict = {}
  cs.forEach((c) => { if (c.decisao?.nivel) porNivel[c.decisao.nivel]++; porStatus[c.status] = (porStatus[c.status] ?? 0) + 1 })
  const fraudes = cs.filter((c) => c.status === 'rejeitado')
  const tok = cs.reduce((a, c) => a + c.llm_uso.tokens_in + c.llm_uso.tokens_out, 0)
  return {
    total: cs.length, autonomos_pct: fim.length ? Math.round((auto.length / fim.length) * 100) : 0,
    tempo_medio_s: tempos.length ? Math.round(tempos.reduce((a, b) => a + b, 0) / tempos.length) : 0,
    fraude_evitada_rs: fraudes.reduce((a, c) => a + (c.dossie?.pedido?.valor_total ?? 0), 0), fraudes_barradas: fraudes.length,
    aprovacoes_pendentes: Object.values(aprovacoes).filter((a) => a.status === 'pendente').length, tempo_medio_aprovacao_s: 38,
    contatos_evitados: cs.filter((c) => c.demo_caso === 3).length * 2 + fim.length,
    por_nivel: porNivel, por_status: porStatus,
    llm: { chamadas: cs.reduce((a, c) => a + c.llm_uso.chamadas, 0), tokens: tok, usd: +cs.reduce((a, c) => a + c.llm_uso.usd, 0).toFixed(4) },
    baseline: { tempo_medio_humano_min: 47, custo_medio_conversa: 9.8 },
  }
}

const analista = {
  gerado_em: '2026-11-02T08:00:00',
  insights: [
    { titulo: 'TransNorte atrasa 3× mais no Piauí', descricao: '41% dos contatos de atraso de out/26 vêm de pedidos TransNorte com destino PI e MA, parados no CD de Teresina.', impacto: '312 contatos/mês', acao: 'Trocar a TransNorte por Jadlog em PI e MA, ou avisar proativamente após 48 h parado.' },
    { titulo: 'Impressora UV-03 concentra avarias de estampa', descricao: 'Capinhas impressas na UV-03 têm 2,4× mais reclamação de estampa descascando que a média.', impacto: '88 trocas/mês', acao: 'Calibrar a UV-03 e segurar o lote L-1029.' },
    { titulo: 'Cobranças em dobro no Pix parcelado', descricao: '27 clientes cobrados duas vezes entre 18 e 21/10 no mesmo gateway.', impacto: 'R$ 4.930 a estornar', acao: 'Estornar em lote e avisar antes que reclamem.' },
  ],
}

const placar = {
  gerado_em: '2026-11-02T07:40:00', total: 20, resolvidos_sozinho: 14, escalados: 5, erros: 1,
  personas: [
    { persona: 'honesto', casos: 6, acertos: 6, autonomos: 6 },
    { persona: 'impaciente', casos: 4, acertos: 4, autonomos: 3 },
    { persona: 'reincidente', casos: 3, acertos: 3, autonomos: 2 },
    { persona: 'fraudador', casos: 4, acertos: 4, autonomos: 3 },
    { persona: 'ambivalente', casos: 3, acertos: 2, autonomos: 0 },
  ],
  erro_encontrado: 'Ambivalente que pedia troca E reembolso recebia os dois. Corrigido: o Porteiro agora pede para escolher.',
}

const filaReal = [
  { canal: 'email', cliente_nome: 'Beatriz Sales', mensagem: 'Quero trocar o tamanho da minha capinha, comprei errado.' },
  { canal: 'whatsapp', cliente_nome: 'Diego Ramos', mensagem: 'Meu pedido GCH60201 ainda não chegou, prazo era ontem.', order_id: 'GCH60201' },
  { canal: 'reclame_aqui', cliente_nome: 'Lúcia Prado', mensagem: 'Cobraram duas vezes no meu cartão!' },
]

export async function mockRequest(method: string, path: string, body?: unknown): Promise<unknown> {
  await sleep(120)
  const b = (body ?? {}) as Dict
  const [rota] = path.split('?')
  const p = rota.split('/').filter(Boolean)
  if (method === 'GET') {
    if (rota === '/api/goxys/casos') return Object.values(casos).map((c) => ({ id: c.id }))
    if (p[2] === 'casos' && p[3]) return clone(casos[p[3]])
    if (rota === '/api/goxys/metricas') return metricas()
    if (rota === '/api/goxys/analista') return analista
    if (rota === '/api/goxys/placar') return placar
    if (rota === '/api/goxys/saude') return { ia: true, slack: true, telegram: false, db: true }
    if (rota === '/api/goxys/mascote') return { agentes: {}, estados: {} }
    if (rota === '/api/aprovacoes') return Object.values(aprovacoes).filter((a) => a.status === 'pendente')
  }
  if (method === 'GET' && rota === '/api/goxys/equipe') return EQUIPE
  if (method === 'GET' && rota === '/api/erp/pedidos') {
    const q = (new URLSearchParams(path.split('?')[1] ?? '').get('q') ?? '').toLowerCase()
    return PEDIDOS.filter((p) => !q || `${p.order_id} ${p.produto} ${p.cliente_nome}`.toLowerCase().includes(q))
  }
  if (method === 'GET' && rota === '/api/goxys/aprendizado') return APRENDIZADO
  const job = rota.match(/^\/api\/goxys\/aprendizado\/jobs\/(.+)$/)
  if (method === 'GET' && job) {
    const j = JOBS[job[1]]
    const p = Math.min(1, (Date.now() - j.t0) / 4000)
    if (p >= 1) { const sg = APRENDIZADO.sugestoes.find((x) => x.id === j.id)!; sg.status = 'validada'; sg.placar = { acertos: 20, total: 20, antes: { acertos: 19, total: 20 } } }
    return { progresso: p, placar: p >= 1 ? { acertos: 20, total: 20, antes: { acertos: 19, total: 20 } } : null }
  }
  if (method === 'POST') {
    const sv = rota.match(/^\/api\/goxys\/aprendizado\/sugestoes\/([^/]+)\/(validar|aplicar)$/)
    if (sv) {
      const sg = APRENDIZADO.sugestoes.find((x) => x.id === sv[1])
      if (!sg) throw new ApiError(404, null, 'sugestão não encontrada')
      if (sv[2] === 'validar') { sg.status = 'validando'; const id = `J${Date.now()}`; JOBS[id] = { id: sg.id, t0: Date.now() }; return { job: id } }
      sg.status = 'aplicada'; (sg as Dict).politica_versao = 'r3'; emit('policy.updated', { politica_versao: 'r3' }); return { politica_versao: 'r3' }
    }
    if (rota === '/api/goxys/demo/sla') return { ok: true, segundos: b.segundos }
    const dz = rota.match(/^\/api\/goxys\/casos\/([^/]+)\/desfazer$/)
    if (dz && casos[dz[1]]) {
      const c = casos[dz[1]]
      if (!c.decisao?.desfazer_ate || new Date(c.decisao.desfazer_ate).getTime() < Date.now()) throw new ApiError(409, { erro: 'prazo_expirado' }, 'prazo expirado')
      c.decisao.desfeito = { por: b.por, por_nome: EQUIPE.find((m) => m.colaborador_id === b.por)?.nome ?? b.por, em: agora() }
      salvar(c); emit('case.undone', { por: b.por }, c.id)
      return clone(c)
    }
  }
  if (method === 'GET' && rota === '/api/reclameaqui/reputacao') return { indice_reputacao: 7.4, classificacao: 'Bom', pct_respondidas: 92, pct_resolvidas: 71, tempo_medio_primeira_resposta_txt: '1 dia e 3 h', respostas_goxys: Object.values(casos).filter((c) => c.canal === 'reclame_aqui' && c.resposta?.texto).length }
  if (method === 'GET' && rota === '/api/reclameaqui/reclamacoes') {
    const cid = new URLSearchParams(path.split('?')[1] ?? '').get('case_id')
    return cid && casos[cid]?.canal === 'reclame_aqui' ? [{ reclamacao_id: `RA-${cid}`, case_id: cid }] : []
  }
  if (method === 'POST' && rota === '/api/reclameaqui/simular-nova') {
    const c = novoCaso({ canal: 'reclame_aqui', mensagem: `${b.titulo}. ${b.texto}`, cliente_nome: 'Consumidor do Reclame Aqui' })
    emit('ra.nova', { reclamacao_id: `RA-${c.id}`, titulo: b.titulo, origem: 'simulada' }, c.id)
    casoLivre(c)
    return { reclamacao_id: `RA-${c.id}`, status_publico: 'Não respondida' }
  }
  if (method === 'POST') {
    if (rota === '/api/goxys/demo/limpar') {
      for (const k of Object.keys(casos)) delete casos[k]
      for (const k of Object.keys(aprovacoes)) delete aprovacoes[k]
      emit('demo.reset', {})
      return { ok: true }
    }
    const cm = rota.match(/^\/api\/goxys\/casos\/([^/]+)\/(passar|mensagens|sugerir-resposta|devolver)$/)
    if (cm && casos[cm[1]]) {
      const c = casos[cm[1]]
      if (cm[2] === 'passar') {
        const p = EQUIPE.find((m) => m.colaborador_id === b.para) ?? EQUIPE[0]
        c.responsavel = { colaborador_id: p.colaborador_id, nome: p.nome, cargo: p.cargo, desde: agora(), nota: b.nota ?? null }
        c.resumo_passagem = [`${c.cliente_nome ?? 'Cliente'}: ${(c.triagem?.intencao ?? 'contato')} no ${nomeCanal(c.canal)}, pedido ${c.order_id ?? 'sem pedido'}.`,
          c.veracidade?.score != null ? `Veracidade ${c.veracidade.score}/100; ${c.decisao?.motivo ?? 'sem decisão ainda'}` : (c.decisao?.motivo ?? 'O Goxy ainda não decidiu.'),
          'Próximo passo: responder o cliente e decidir a ação.']
        c.status = 'humano'; c.mascot_state = 'humano'
        salvar(c); emit('case.handoff', { para: c.responsavel }, c.id)
        return clone(c)
      }
      if (cm[2] === 'mensagens') { c.mensagens.push({ autor: 'agente', nome: b.nome ?? c.responsavel?.nome, texto: b.texto, ts: agora() }); salvar(c); return clone(c) }
      if (cm[2] === 'sugerir-resposta') { await sleep(700); return { texto: `Oi, ${(c.cliente_nome ?? '').split(' ')[0] || 'tudo bem'}! Aqui é ${c.responsavel?.nome.split(' ')[0] ?? 'a equipe'}, da Gocase. Já estou com o seu caso e te dou um retorno ainda hoje.` } }
      if (cm[2] === 'devolver') {
        if (b.nota) c.mensagens.push({ autor: 'agente', nome: c.responsavel?.nome, texto: b.nota, ts: agora() })
        c.responsavel = null
        if (b.instrucao === 'encerrar') { c.status = 'resolvido'; c.mascot_state = 'resolvido'; c.encerrado_em = agora() } else { c.status = 'em_andamento'; c.mascot_state = 'investigando' }
        salvar(c); emit('case.returned', { instrucao: b.instrucao }, c.id)
        if (b.instrucao !== 'encerrar') setTimeout(() => { c.status = 'resolvido'; c.mascot_state = 'resolvido'; c.encerrado_em = agora(); salvar(c) }, 2500)
        return clone(c)
      }
    }
    const m = rota.match(/^\/api\/aprovacoes\/([^/]+)\/decisao$/)
    if (m) {
      try { return decidir(m[1], b) } catch (e: any) {
        throw new ApiError(e.status ?? 500, e.body, e.body?.mensagem ?? 'erro')
      }
    }
    if (rota === '/api/goxys/simular/todos') {
      ;(async () => { for (const r of roteiros) { r(); await sleep(1500) } })()
      return { ok: true }
    }
    const sim = rota.match(/^\/api\/goxys\/simular\/(\d)$/)
    if (sim) { roteiros[Number(sim[1]) - 1]?.(); return { ok: true } }
    if (rota === '/api/goxys/fila/importar') {
      const ids = filaReal.map((f) => novoCaso({ ...f, conversation_id: `AT${Math.floor(Math.random() * 90000) + 10000}` }).id)
      return { importados: ids.length, case_ids: ids }
    }
    const proc = rota.match(/^\/api\/goxys\/casos\/([^/]+)\/processar$/)
    if (proc && casos[proc[1]]) { casoLivre(casos[proc[1]]); return { case_id: proc[1] } }
    if (rota === '/api/goxys/casos') {
      const c = novoCaso({ canal: b.canal ?? 'painel', mensagem: b.mensagem ?? '', order_id: b.order_id || null, customer_id: b.customer_id || null, cliente_nome: 'Cliente do painel' })
      casoLivre(c)
      return { case_id: c.id }
    }
  }
  throw new ApiError(404, null, `mock sem rota ${method} ${path}`)
}

export function mockStream(onEvent: Listener): () => void {
  historico.forEach(onEvent)
  listeners.add(onEvent)
  return () => listeners.delete(onEvent)
}

// ?mock=1&auto=1 dispara os 5 casos ao abrir (útil para ensaio e screenshots)
if (new URLSearchParams(window.location.search).get('auto') === '1') {
  setTimeout(() => { void mockRequest('POST', '/api/goxys/simular/todos') }, 400)
}
