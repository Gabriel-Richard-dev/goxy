// Fila de exibição: cada passo fica pelo menos MIN_RODANDO ms como "rodando" na tela,
// mesmo que o evento finished chegue antes, e os passos entram em ordem sem pular etapas.
import { useEffect, useState } from 'react'
import type { Case, TimelineStep } from './types'

const MIN_RODANDO = 1200
const ESCALONAR = 350
const BOOT = Date.now()
const HISTORICO_MS = 1500 // o que chega no replay inicial do SSE não anima

const vistos = new Map<string, { visto: number; fim?: number }>()
const FIM = ['ok', 'alerta', 'falhou', 'pulado']

/** Chamar a cada mudança de casos (todos), para registrar quando cada passo foi visto. */
export function registrarCasos(cases: Case[]) {
  const agora = Date.now()
  const historico = agora - BOOT < HISTORICO_MS
  for (const c of cases) {
    const t = Date.parse(c.atualizado_em)
    const velho = !Number.isNaN(t) && agora - t > 8000 // já estava assim antes de abrirmos a tela: não reencena
    const fechado = (!!c.encerrado_em && historico) || velho
    ;(c.timeline ?? []).forEach((s, i) => {
      const k = `${c.id}:${i}`
      if (s.status === 'pendente') return // só conta a partir do momento em que o passo começa
      let v = vistos.get(k)
      if (!v) { v = { visto: historico || fechado ? 0 : agora }; vistos.set(k, v) }
      if (FIM.includes(s.status) && v.fim == null) v.fim = v.visto === 0 ? 0 : agora
    })
  }
}

function calcular(c: Case, agora: number): { caso: Case; proxima: number | null } {
  let proxima: number | null = null
  const marca = (t: number) => { if (t > agora && (proxima == null || t < proxima)) proxima = t }
  let inicioAnterior = -Infinity
  let atrasado = false
  const timeline: TimelineStep[] = (c.timeline ?? []).map((s, i) => {
    const v = vistos.get(`${c.id}:${i}`) ?? { visto: 0, fim: 0 }
    if (s.status === 'pendente') return s
    const inicio = Math.max(v.visto, inicioAnterior + ESCALONAR)
    inicioAnterior = inicio
    if (agora < inicio) { marca(inicio); atrasado = true; return { ...s, status: 'pendente' as const, titulo: '' } }
    if (s.status === 'rodando') return s
    const fim = Math.max(v.fim ?? 0, inicio + MIN_RODANDO)
    if (v.visto !== 0 && agora < fim) { marca(fim); atrasado = true; return { ...s, status: 'rodando' as const } }
    return s
  })
  if (!atrasado) return { caso: c, proxima: null }
  const final = ['resolvido', 'rejeitado', 'humano', 'aguardando_aprovacao'].includes(c.status)
  const detetiveFeito = timeline.some((s) => s.agent === 'detetive' && FIM.includes(s.status))
  const caso: Case = {
    ...c,
    timeline,
    status: final ? 'em_andamento' : c.status,
    mascot_state: c.mascot_state === 'fraude' && detetiveFeito ? 'fraude' : final ? 'investigando' : c.mascot_state,
    resposta: {},
    decisao: timeline.some((s) => s.agent === 'maestro' && /n[íi]vel/i.test(s.titulo) && FIM.includes(s.status)) ? c.decisao : {},
    veracidade: detetiveFeito ? c.veracidade : { ...c.veracidade, comparacao: undefined },
    aprovacao: final ? {} : c.aprovacao,
  }
  return { caso, proxima }
}

/** O caso como deve aparecer agora (com o atraso mínimo de exibição). */
export function useCasoExibido(c: Case | null): Case | null {
  const [agora, setAgora] = useState(Date.now())
  const r = c ? calcular(c, agora) : null
  const proxima = r?.proxima ?? null
  useEffect(() => {
    if (proxima == null) return
    const t = window.setTimeout(() => setAgora(Date.now()), Math.max(16, proxima - Date.now() + 5))
    return () => window.clearTimeout(t)
  }, [proxima, c])
  useEffect(() => { setAgora(Date.now()) }, [c])
  return r?.caso ?? null
}
