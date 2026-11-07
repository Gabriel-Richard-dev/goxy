import type { CaseStatus } from './types'

export const brl = (v?: number | null) =>
  v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

export const hora = (iso?: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const dataHora = (iso?: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export const duracao = (s?: number | null) => {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

export const NOME_CANAL: Record<string, string> = {
  whatsapp: 'WhatsApp', email: 'E-mail', telegram: 'Telegram', reclame_aqui: 'Reclame Aqui', painel: 'Painel', slack: 'Slack', chat: 'Chat',
}
export const nomeCanal = (c?: string | null) => NOME_CANAL[c ?? ''] ?? c ?? ''

export type Tom = 'ok' | 'espera' | 'critico' | 'agora' | 'neutro' | 'estrutura'

export const STATUS: Record<CaseStatus, { rotulo: string; tom: Tom }> = {
  novo: { rotulo: 'Novo', tom: 'neutro' },
  em_andamento: { rotulo: 'Em andamento', tom: 'agora' },
  aguardando_aprovacao: { rotulo: 'Esperando aprovação', tom: 'espera' },
  resolvido: { rotulo: 'Resolvido', tom: 'ok' },
  rejeitado: { rotulo: 'Fraude barrada', tom: 'critico' },
  humano: { rotulo: 'Com uma pessoa', tom: 'estrutura' },
  erro: { rotulo: 'Erro', tom: 'critico' },
}

export const TOM_CLASSE: Record<Tom, string> = {
  ok: 'bg-ok-suave text-ok',
  espera: 'bg-espera-suave text-espera-texto',
  critico: 'bg-critico-suave text-critico',
  agora: 'bg-agora-suave text-texto',
  neutro: 'bg-sup2 text-suave',
  estrutura: 'bg-sup2 text-estrutura',
}

export const NIVEL: Record<string, { rotulo: string; desc: string; tom: Tom }> = {
  A: { rotulo: 'A', desc: 'Age sozinho', tom: 'ok' },
  B: { rotulo: 'B', desc: 'Age e avisa', tom: 'ok' },
  C: { rotulo: 'C', desc: 'Pede aprovação', tom: 'espera' },
  D: { rotulo: 'D', desc: 'Pessoa assume', tom: 'critico' },
}

export const titulo = (c: { triagem?: Record<string, any>; mensagem?: string; demo_caso?: number | null }) => {
  const DEMO: Record<number, string> = { 1: 'Avaria de baixo valor', 2: 'Avaria com foto suspeita', 3: 'Atraso na entrega', 4: 'Reembolso alto', 5: 'Ameaça de processo' }
  if (c.demo_caso && DEMO[c.demo_caso]) return DEMO[c.demo_caso]
  const INT: Record<string, string> = { avaria: 'Avaria', atraso: 'Atraso na entrega', reembolso: 'Pedido de reembolso', troca: 'Troca', duvida: 'Dúvida', reclamacao: 'Reclamação', cobranca: 'Cobrança' }
  const i = c.triagem?.intencao
  if (i) return INT[i] ?? i.charAt(0).toUpperCase() + i.slice(1).replace(/_/g, ' ')
  const m = (c.mensagem ?? '').trim()
  return m ? (m.length > 34 ? m.slice(0, 34) + '…' : m) : 'Contato novo'
}

export function cx(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(' ')
}
