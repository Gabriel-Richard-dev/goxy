import { createContext, useContext, useEffect, useReducer, useRef, type ReactNode } from 'react'
import { api, connectStream, type StreamStatus } from './api'
import type { Aprovacao, BusEvent, Case, Dict, Mascote } from './types'
import { normalizarMascote } from './mascot'

export interface State {
  cases: Record<string, Case>
  eventos: Record<string, BusEvent[]>   // eventos por caso (tool.called, llm.call, policy...)
  aprovacoes: Record<string, Aprovacao>
  lastId: number
  stream: StreamStatus
  mascote: Mascote
  ultimoCriado: string | null
}

type Action =
  | { t: 'evento'; e: BusEvent }
  | { t: 'stream'; s: StreamStatus }
  | { t: 'cases'; cases: Case[] }
  | { t: 'aprovacoes'; lista: Aprovacao[] }
  | { t: 'mascote'; m: Mascote }

const inicial: State = {
  cases: {}, eventos: {}, aprovacoes: {}, lastId: 0, stream: 'conectando',
  mascote: { agentes: {}, estados: {} }, ultimoCriado: null,
}

function patchCase(s: State, id: string | null, fn: (c: Case) => Case): State {
  if (!id || !s.cases[id]) return s
  return { ...s, cases: { ...s.cases, [id]: fn(s.cases[id]) } }
}

function aprovacaoDeEvento(e: BusEvent): Aprovacao {
  const d = e.data
  return {
    aprovacao_id: d.aprovacao_id, case_id: e.case_id, titulo: d.titulo ?? 'Aprovação',
    resumo: d.resumo ?? '', nivel: d.nivel, valor: d.valor, acao_proposta: d.acao_proposta,
    evidencias: d.evidencias ?? [], expira_em: d.expira_em, status: 'pendente', pedido_em: e.ts,
  }
}

function reducer(s: State, a: Action): State {
  switch (a.t) {
    case 'stream': return { ...s, stream: a.s }
    case 'mascote': return { ...s, mascote: a.m }
    case 'cases': {
      const cases = { ...s.cases }
      for (const c of a.cases) if (!cases[c.id] || cases[c.id].atualizado_em <= c.atualizado_em) cases[c.id] = c
      return { ...s, cases }
    }
    case 'aprovacoes': {
      const aprovacoes = { ...s.aprovacoes }
      for (const ap of a.lista) aprovacoes[ap.aprovacao_id] = { ...aprovacoes[ap.aprovacao_id], ...ap }
      return { ...s, aprovacoes }
    }
    case 'evento': {
      const e = a.e
      if (e.id && e.id <= s.lastId) return s
      let n: State = { ...s, lastId: Math.max(s.lastId, e.id || 0) }
      if (e.case_id && !e.type.startsWith('case.')) {
        const lista = n.eventos[e.case_id] ?? []
        n = { ...n, eventos: { ...n.eventos, [e.case_id]: [...lista, e].slice(-300) } }
      }
      const d = e.data as Dict
      switch (e.type) {
        case 'demo.reset':
          return { ...inicial, stream: n.stream, mascote: n.mascote, lastId: n.lastId }
        case 'case.created':
        case 'case.updated':
          if (d.case?.id) {
            n = { ...n, cases: { ...n.cases, [d.case.id]: d.case } }
            if (e.type === 'case.created') n.ultimoCriado = d.case.id
          }
          return n
        case 'agent.started':
          return patchCase(n, e.case_id, (c) => upsertStep(c, e.agent, { status: 'rodando', titulo: d.titulo ?? '', inicio: e.ts }))
        case 'agent.finished':
          return patchCase(n, e.case_id, (c) => upsertStep(c, e.agent, {
            status: d.status ?? 'ok', titulo: d.titulo ?? '', detalhe: d.detalhe ?? '',
            evidencias: d.evidencias ?? [], duracao_ms: d.duracao_ms, fim: e.ts,
          }))
        case 'mascot.state':
          return patchCase(n, e.case_id, (c) => ({ ...c, mascot_state: d.state }))
        case 'message.sent':
          return patchCase(n, e.case_id, (c) => (c.resposta?.texto ? c : { ...c, resposta: { texto: d.texto, canal: d.canal, message_id: d.message_id } }))
        case 'case.closed':
          return patchCase(n, e.case_id, (c) => ({ ...c, status: d.status ?? c.status }))
        case 'approval.requested':
          if (!d.aprovacao_id) return n
          return { ...n, aprovacoes: { ...n.aprovacoes, [d.aprovacao_id]: aprovacaoDeEvento(e) } }
        case 'approval.decided': {
          const ant = n.aprovacoes[d.aprovacao_id]
          const ap: Aprovacao = {
            ...(ant ?? aprovacaoDeEvento(e)), status: 'decidida', decisao: d.decisao, aprovador: d.aprovador,
            canal: d.canal, ajuste: d.ajuste, tempo_resposta_s: d.tempo_resposta_s,
          }
          return { ...n, aprovacoes: { ...n.aprovacoes, [d.aprovacao_id]: ap } }
        }
        case 'approval.escalated': {
          const ant = n.aprovacoes[d.aprovacao_id]
          if (!ant || ant.status === 'decidida') return n
          return { ...n, aprovacoes: { ...n.aprovacoes, [d.aprovacao_id]: { ...ant, status: 'escalada', escalada_para: typeof d.para === 'object' ? d.para : { nome: d.para } } } }
        }
      }
      return n
    }
  }
}

function upsertStep(c: Case, agent: string | null, patch: Dict): Case {
  if (!agent) return c
  const tl = [...(c.timeline ?? [])]
  let i = -1
  for (let k = tl.length - 1; k >= 0; k--) if (tl[k].agent === agent) { i = k; break }
  const feito = i >= 0 && ['ok', 'alerta', 'falhou', 'pulado'].includes(tl[i].status)
  if (patch.status === 'rodando') {
    // o mesmo agente pode aparecer mais de uma vez (ex.: Maestro); um passo novo vira linha nova
    if (i >= 0 && !feito) tl[i] = { ...tl[i], ...patch }
    else if (i >= 0 && feito && tl[i].titulo === patch.titulo) return c
    else tl.push({ agent, status: 'pendente', titulo: '', detalhe: '', evidencias: [], ...patch })
  } else if (i >= 0) tl[i] = { ...tl[i], ...patch }
  else tl.push({ agent, status: 'pendente', titulo: '', detalhe: '', evidencias: [], ...patch })
  return { ...c, timeline: tl }
}

const Ctx = createContext<{ s: State; recarregar: () => void } | null>(null)

export function GoxyProvider({ children }: { children: ReactNode }) {
  const [s, dispatch] = useReducer(reducer, inicial)
  const lastRef = useRef(0)
  lastRef.current = s.lastId

  const carregar = async () => {
    try {
      const lista = await api.get<{ id: string }[]>('/api/goxys/casos')
      const completos = await Promise.all(
        lista.slice(0, 60).map((c) => api.get<Case>(`/api/goxys/casos/${c.id}`).catch(() => null)),
      )
      dispatch({ t: 'cases', cases: completos.filter(Boolean) as Case[] })
    } catch { /* backend ainda subindo */ }
    try {
      const pend = await api.get<Dict[]>('/api/aprovacoes?status=pendente')
      dispatch({
        t: 'aprovacoes',
        lista: (pend ?? []).map((p) => ({
          aprovacao_id: p.aprovacao_id ?? p.id, case_id: p.case_id ?? null, titulo: p.titulo ?? 'Aprovação',
          resumo: p.resumo ?? '', nivel: p.nivel, valor: p.valor, acao_proposta: p.acao_proposta,
          evidencias: typeof p.evidencias === 'string' ? safeJson(p.evidencias) : (p.evidencias ?? []),
          expira_em: p.expira_em, status: 'pendente', pedido_em: p.criado_em ?? '',
        })),
      })
    } catch { /* idem */ }
  }

  const carregarMascote = async () => {
    let m: Mascote = { agentes: {}, estados: {} }
    try { m = normalizarMascote(await api.get<Dict>('/api/goxys/mascote')) } catch { /* sem rota ainda */ }
    if (!Object.keys(m.agentes).length && !Object.keys(m.estados).length) {
      try {
        const r = await fetch('/assets-goxys/goxys/manifest.json')
        if (r.ok) m = normalizarMascote(await r.json())
      } catch { /* sem manifesto */ }
    }
    dispatch({ t: 'mascote', m })
  }

  useEffect(() => {
    carregar()
    carregarMascote()
    const fechar = connectStream(() => lastRef.current, (e) => dispatch({ t: 'evento', e }), (st) => {
      dispatch({ t: 'stream', s: st })
    })
    // o usuário ainda está gerando os personagens: procura novos arquivos de tempos em tempos
    const t = window.setInterval(carregarMascote, 30000)
    return () => { fechar(); window.clearInterval(t) }
  }, [])

  return <Ctx.Provider value={{ s, recarregar: carregar }}>{children}</Ctx.Provider>
}

function safeJson(x: string): unknown[] {
  try { const v = JSON.parse(x); return Array.isArray(v) ? v : [v] } catch { return [] }
}

export function useGoxy() {
  const v = useContext(Ctx)
  if (!v) throw new Error('GoxyProvider ausente')
  return v
}

/** Aprovações combinando eventos e o snapshot do caso. */
export function listarAprovacoes(s: State): Aprovacao[] {
  const mapa: Record<string, Aprovacao> = { ...s.aprovacoes }
  for (const c of Object.values(s.cases)) {
    const ap = c.aprovacao
    if (!ap?.aprovacao_id) continue
    const base: Aprovacao = {
      aprovacao_id: ap.aprovacao_id, case_id: c.id, titulo: ap.titulo ?? 'Aprovação', resumo: ap.resumo ?? '',
      nivel: c.decisao?.nivel, valor: c.decisao?.valor, acao_proposta: c.decisao?.acao, evidencias: [],
      expira_em: ap.expira_em, status: 'pendente', pedido_em: c.atualizado_em,
    }
    const m: Aprovacao = { ...base, ...(mapa[ap.aprovacao_id] ?? {}), case_id: c.id }
    if (ap.status && ap.status !== 'pendente' && ap.decisao && m.status !== 'decidida') {
      Object.assign(m, { status: 'decidida', decisao: ap.decisao, aprovador: ap.aprovador, canal: ap.canal, tempo_resposta_s: ap.tempo_resposta_s })
    }
    mapa[ap.aprovacao_id] = m
  }
  return Object.values(mapa).sort((a, b) => (b.pedido_em || '').localeCompare(a.pedido_em || ''))
}
