import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Case, Mascote } from '../types'
import { BASE_GOXY, corAgente, imgPequena, nomeAgente } from '../mascot'
import { cx } from '../lib'

// Posições fixas (estável e legível): o Maestro no meio, o fluxo gira no sentido horário.
// Esquerda para a direita, no fluxo real: Porteiro -> (Rastreador || Detetive || Analista) -> Política -> Executor / Aprovação / Pessoa -> Voz.
const POS: Record<string, [number, number]> = {
  porteiro: [90, 205],
  rastreador: [300, 75],
  detetive: [300, 205],
  analista: [300, 335],
  politica: [500, 205],
  executor: [720, 205],
  mensageiro: [620, 335],
  humano: [820, 335],
  voz: [925, 205],
}
const R = 34
const NOS = Object.keys(POS)
const NOME: Record<string, string> = { politica: 'Política', humano: 'Pessoa' }
const nomeNo = (n: string) => NOME[n] ?? nomeAgente(n)
const corNo = (n: string) => (n === 'humano' ? 'var(--espera)' : n === 'politica' ? 'var(--ag-politica)' : corAgente(n))

type EstadoNo = 'fora' | 'pendente' | 'rodando' | 'ok' | 'alerta' | 'falhou'
export interface Aresta { de: string; para: string; peso?: number; tipo?: 'plano' | 'aprovacao' | 'retorno' }

/** Nó do plano → nó do grafo. */
function noDoPlano(p: Record<string, any>): string | null {
  if (p.id === 'politica') return 'politica'
  if (p.id === 'acao' || String(p.agent ?? '').includes('|')) return p.ramo ?? null
  return p.agent ?? p.id ?? null
}

/** Arestas do caso: plano real (depende_de) + aprovação humana. */
export function arestasDoCaso(c: Case): Aresta[] {
  const out: Aresta[] = []
  const add = (de: string | null, para: string | null, tipo: Aresta['tipo'] = 'plano') => {
    if (!de || !para || de === para || !POS[de] || !POS[para]) return
    if (!out.some((a) => a.de === de && a.para === para)) out.push({ de, para, tipo })
  }
  const plano = c.plano ?? []
  if (plano.length) {
    const porId: Record<string, string | null> = {}
    for (const p of plano) porId[p.id ?? p.agent] = noDoPlano(p)
    for (const p of plano) for (const d of p.depende_de ?? []) add(porId[d] ?? d, noDoPlano(p))
  } else {
    // sem plano (ex.: mock antigo): sequência da linha do tempo
    const seq = (c.timeline ?? []).map((s) => s.agent).filter((a) => a !== 'maestro')
    for (let i = 1; i < seq.length; i++) add(seq[i - 1], seq[i])
  }
  const usados = new Set((c.timeline ?? []).map((s) => s.agent))
  if (c.decisao?.nivel) {
    usados.add('politica')
    if (!out.some((a) => a.para === 'politica')) {
      if (usados.has('rastreador')) add('rastreador', 'politica')
      if (usados.has('detetive')) add('detetive', 'politica')
    }
    for (const a of ['executor', 'mensageiro'] as const) if (usados.has(a)) add('politica', a)
    if (usados.has('voz') && !out.some((x) => x.para === 'voz')) add(usados.has('executor') ? 'executor' : usados.has('mensageiro') ? 'mensageiro' : 'politica', 'voz')
  }
  if (c.aprovacao?.aprovacao_id || c.status === 'aguardando_aprovacao' || c.status === 'humano' || usados.has('mensageiro')) {
    add('mensageiro', 'humano', 'aprovacao')
    if (c.aprovacao?.status && c.aprovacao.status !== 'pendente' && usados.has('executor')) add('humano', 'executor', 'retorno')
  }
  if (usados.has('analista')) add('porteiro', 'analista')
  // só UMA aresta entra em Pessoa: Mensageiro -> Pessoa (o nível D também passa pelo Mensageiro, que chama a pessoa)
  for (let k = out.length - 1; k >= 0; k--) {
    const a = out[k]
    if (a.para === 'humano' && a.de !== 'mensageiro') { out.splice(k, 1); add(a.de, 'mensageiro'); add('mensageiro', 'humano', 'aprovacao') }
  }
  // depois da pessoa, quem responde é a Voz: Mensageiro -> Pessoa -> Voz (sem cruzar com o Executor)
  const k = out.findIndex((a) => a.de === 'mensageiro' && a.para === 'voz')
  if (k >= 0 && out.some((a) => a.para === 'humano')) { out.splice(k, 1); add('humano', 'voz') }
  return out
}

export function estadosDoCaso(c: Case): Record<string, EstadoNo> {
  const e: Record<string, EstadoNo> = {}
  for (const s of c.timeline ?? []) {
    const st = s.status === 'pulado' ? 'pendente' : (s.status as EstadoNo)
    // o último passo de cada agente vale; "rodando" sempre ganha
    if (e[s.agent] !== 'rodando') e[s.agent] = st
  }
  for (const p of c.plano ?? []) {
    const n = noDoPlano(p)
    if (n && !e[n]) e[n] = p.status === 'rodando' ? 'rodando' : p.status === 'ok' ? 'ok' : 'pendente'
  }
  if (c.decisao?.nivel) e.politica = c.decisao.nivel === 'A' || c.decisao.nivel === 'B' ? 'ok' : 'alerta'
  if (c.status === 'aguardando_aprovacao') e.humano = 'rodando'
  else if (c.status === 'humano') e.humano = 'rodando'
  else if (c.aprovacao?.status && c.aprovacao.status !== 'pendente') e.humano = 'ok'
  return e
}

function caminho(de: string, para: string, curva = 0.08) {
  const [x1, y1] = POS[de]
  const [x2, y2] = POS[para]
  const dx = x2 - x1, dy = y2 - y1
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len, uy = dy / len
  const sx = x1 + ux * (R + 4), sy = y1 + uy * (R + 4)
  const ex = x2 - ux * (R + 10), ey = y2 - uy * (R + 10)
  // curva leve, sempre para o mesmo lado
  const cx_ = (sx + ex) / 2 - uy * len * curva, cy_ = (sy + ey) / 2 + ux * len * curva
  return `M${sx.toFixed(1)},${sy.toFixed(1)} Q${cx_.toFixed(1)},${cy_.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`
}

function meio(de: string, para: string, curva: number): [number, number] {
  const m = caminho(de, para, curva).match(/M([\d.-]+),([\d.-]+) Q([\d.-]+),([\d.-]+) ([\d.-]+),([\d.-]+)/)
  if (!m) return [(POS[de][0] + POS[para][0]) / 2, (POS[de][1] + POS[para][1]) / 2]
  const [sx, sy, qx, qy, ex, ey] = m.slice(1).map(Number)
  return [0.25 * sx + 0.5 * qx + 0.25 * ex, 0.25 * sy + 0.5 * qy + 0.25 * ey]
}

/** Partícula que corre pela aresta uma vez (rAF + getPointAtLength: estável em qualquer navegador). */
function Particula({ d, cor, onFim }: { d: string; cor: string; onFim: () => void }) {
  const path = useRef<SVGPathElement>(null)
  const dot = useRef<SVGCircleElement>(null)
  useEffect(() => {
    const p = path.current, c = dot.current
    if (!p || !c) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { onFim(); return }
    const L = p.getTotalLength()
    const t0 = performance.now()
    let raf = 0
    const passo = (t: number) => {
      const k = Math.min(1, (t - t0) / 900)
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
      const pt = p.getPointAtLength(e * L)
      c.setAttribute('cx', String(pt.x)); c.setAttribute('cy', String(pt.y))
      if (k < 1) raf = requestAnimationFrame(passo)
      else onFim()
    }
    raf = requestAnimationFrame(passo)
    return () => cancelAnimationFrame(raf)
  }, [d])
  return (
    <g>
      <path ref={path} d={d} fill="none" stroke="none" />
      <circle ref={dot} r={7} fill={cor} style={{ filter: `drop-shadow(0 0 6px ${cor})` }} />
    </g>
  )
}

interface Props {
  mascote: Mascote
  arestas: Aresta[]
  estados?: Record<string, EstadoNo>
  rotulos?: Record<string, string>      // texto abaixo do nome (ex.: "12 casos, 92%")
  agregado?: boolean
  selecionado?: string | null
  onNo?: (n: string) => void
  chaveCaso?: string | null             // troca de caso zera as partículas
  className?: string
  resumos?: Record<string, { nome: string; papel: string; status: string; statusChave: string; frase: string; duracao?: string; numeros: [string, string][] }>
}

export function Grafo({ mascote, arestas, estados = {}, rotulos = {}, agregado, selecionado, onNo, chaveCaso, className, resumos }: Props) {
  // cartão-resumo ao passar o mouse/focar (300 ms de atraso); no touch, 1º toque abre o cartão, 2º abre a gaveta
  const [hover, setHover] = useState<string | null>(null)
  const [rectNo, setRectNo] = useState<DOMRect | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const toque = useRef(false)
  const mostrar = (n: string | null, atraso = 300) => {
    window.clearTimeout(timer.current)
    if (n == null) { timer.current = window.setTimeout(() => setHover(null), 120); return }
    timer.current = window.setTimeout(() => setHover(n), atraso)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const uid = useId().replace(/:/g, '')
  const [particulas, setParticulas] = useState<{ k: string; de: string; para: string }[]>([])
  const anterior = useRef<Record<string, EstadoNo>>({})
  const caso = useRef<string | null | undefined>(undefined)

  // quando um nó começa (ou termina sem termos visto começar), o trabalho "viaja" das dependências até ele
  useEffect(() => {
    if (agregado) return
    if (caso.current !== chaveCaso) { caso.current = chaveCaso; anterior.current = { ...estados }; setParticulas([]); return }
    const novas: { k: string; de: string; para: string }[] = []
    for (const n of NOS) {
      const antes = anterior.current[n], agora = estados[n]
      const comecou = agora === 'rodando' && antes !== 'rodando'
      const pulou = (agora === 'ok' || agora === 'alerta') && (!antes || antes === 'pendente' || antes === 'fora')
      if (comecou || pulou) {
        for (const a of arestas) if (a.para === n) novas.push({ k: `${a.de}>${n}>${Date.now()}`, de: a.de, para: n })
      }
    }
    anterior.current = { ...estados }
    if (novas.length) setParticulas((p) => [...p, ...novas].slice(-12))
  }, [estados, arestas, chaveCaso, agregado])

  const maxPeso = useMemo(() => Math.max(1, ...arestas.map((a) => a.peso ?? 1)), [arestas])
  const presentes = new Set<string>([...arestas.flatMap((a) => [a.de, a.para]), ...Object.keys(estados)])

  return (
    <div className="relative">
    <svg viewBox="0 0 1010 420" className={cx('h-auto w-full select-none', className)} role="group" aria-label="Grafo do time de agentes">
      <defs>
        <marker id={`${uid}seta`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,1 L9,5 L0,9 z" fill="var(--texto-suave)" />
        </marker>
        <marker id={`${uid}seta-ambar`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,1 L9,5 L0,9 z" fill="var(--espera)" />
        </marker>
        {NOS.map((n) => <clipPath key={n} id={`${uid}clip-${n}`}><circle cx={POS[n][0]} cy={POS[n][1]} r={R - 3} /></clipPath>)}
      </defs>


      {arestas.map((a) => {
        const d = caminho(a.de, a.para, agregado ? 0.04 : 0.08)
        const ambar = a.tipo === 'aprovacao' || a.tipo === 'retorno'
        const ativa = !agregado && (estados[a.para] === 'rodando' || (a.tipo === 'aprovacao' && estados.humano === 'rodando'))
        const feita = !agregado && ['ok', 'alerta', 'falhou'].includes(estados[a.para] ?? '')
        const pausa = !agregado && a.tipo === 'aprovacao' && estados.humano === 'rodando'
        const w = agregado ? 1.5 + ((a.peso ?? 1) / maxPeso) * 5.5 : ativa ? 3 : 1.8
        // só a aresta ativa (azul) ou a da pausa (laranja) ganha cor; o resto é cinza
        const cor = agregado ? (ambar ? 'var(--espera)' : 'var(--texto-suave)') : pausa ? 'var(--espera)' : ativa ? 'var(--azul)' : feita ? '#aeb5c0' : 'var(--borda)'
        return (
          <g key={`${a.de}-${a.para}`}>
            <path d={d} fill="none" stroke={cor} strokeWidth={w} strokeLinecap="round" opacity={agregado ? 0.55 : 1}
              markerEnd={agregado ? undefined : pausa ? `url(#${uid}seta-ambar)` : `url(#${uid}seta)`}
              className={cx(ativa && 'aresta-viva')} strokeDasharray={ativa ? '8 8' : a.tipo === 'retorno' ? '4 5' : undefined} />
            {agregado && a.peso != null && (() => {
              const [mx, my] = meio(a.de, a.para, 0.04)
              const lw = 12 + String(a.peso).length * 8
              return (
                <g>
                  <rect x={mx - lw / 2} y={my - 11} width={lw} height={22} rx={11} fill="var(--superficie)" stroke={ambar ? 'var(--espera)' : 'var(--borda)'} />
                  <text x={mx} y={my + 5} textAnchor="middle" className="num" fontSize="15" fontWeight={700} fill="var(--texto)">{a.peso}</text>
                </g>
              )
            })()}
          </g>
        )
      })}

      {false && particulas.map((p) => (
        <Particula key={p.k} d={caminho(p.de, p.para)} cor={corNo(p.para)} onFim={() => setParticulas((x) => x.filter((y) => y.k !== p.k))} />
      ))}

      {NOS.map((n) => {
        const [x, y] = POS[n]
        const st: EstadoNo = estados[n] ?? (presentes.has(n) ? 'pendente' : 'fora')
        const cor = corNo(n)
        const apagado = !agregado && (st === 'fora' || st === 'pendente')
        const r = R
        const img = n === 'humano' ? null : (imgPequena(mascote.agentes[n]) ?? mascote.base_sm ?? mascote.base ?? BASE_GOXY)
        return (
          <g key={n}
            onPointerDown={(e) => { toque.current = e.pointerType === 'touch' }}
            onClick={() => {
              if (toque.current && resumos && hover !== n) { setHover(n); return }
              setHover(null); onNo?.(n)
            }}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setHover(null); onNo?.(n) } }}
            onMouseEnter={(e) => { if (resumos) { setRectNo(e.currentTarget.getBoundingClientRect()); mostrar(n) } }} onMouseLeave={() => resumos && mostrar(null)}
            onFocus={(e) => { if (resumos) { setRectNo(e.currentTarget.getBoundingClientRect()); mostrar(n, 0) } }} onBlur={() => resumos && mostrar(null)}
            tabIndex={onNo ? 0 : -1} role={onNo ? 'button' : undefined}
            aria-label={resumos?.[n] ? `${resumos[n].nome}, ${resumos[n].status}, ${resumos[n].frase}` : `${nomeNo(n)}${rotulos[n] ? `: ${rotulos[n]}` : ''}`}
            className={cx(onNo && 'cursor-pointer', 'no-grafo')} style={{ ['--cor' as string]: cor, opacity: apagado ? (st === 'fora' ? 0.28 : 0.5) : 1 }}>
            {st === 'rodando' && <circle cx={x} cy={y} r={r + 6} fill="none" stroke={n === 'humano' ? 'var(--espera)' : 'var(--azul)'} strokeWidth={2.5} className="anel-vivo" />}
            {selecionado === n && <circle cx={x} cy={y} r={r + 11} fill="none" stroke="var(--estrutura)" strokeWidth={2} strokeDasharray="3 4" />}
            <circle cx={x} cy={y} r={r} fill={agregado ? `color-mix(in srgb, ${cor} 22%, var(--superficie))` : 'var(--superficie)'}
              stroke={agregado ? cor : st === 'rodando' ? (n === 'humano' ? 'var(--espera)' : 'var(--azul)') : 'var(--borda)'} strokeWidth={st === 'rodando' ? 3 : 1.5} />
            {img ? (
              <image href={img} x={x - r + 3} y={y - r + 3} width={(r - 3) * 2} height={(r - 3) * 2} clipPath={`url(#${uid}clip-${n})`} preserveAspectRatio="xMidYMid slice" style={{ filter: apagado ? 'grayscale(1)' : undefined }} />
            ) : (
              <g stroke="var(--texto)" strokeWidth={2.4} fill="none" strokeLinecap="round">
                <circle cx={x} cy={y - 8} r={8} />
                <path d={`M${x - 15},${y + 18} q15,-20 30,0`} />
              </g>
            )}
            {/* selo só onde há problema real: falha, ou alerta do Detetive (evidência fraca) */}
            {!agregado && (st === 'falhou' || (st === 'alerta' && n === 'detetive')) && (
              <g style={{ transformOrigin: `${x + r * 0.72}px ${y - r * 0.72}px` }}>
                <circle cx={x + r * 0.72} cy={y - r * 0.72} r={10} fill={st === 'falhou' ? 'var(--critico)' : 'var(--espera)'} stroke="var(--superficie)" strokeWidth={2} />
                {<path d={`M${x + r * 0.72},${y - r * 0.72 - 4} v4 M${x + r * 0.72},${y - r * 0.72 + 4} v.1`} stroke={st === 'falhou' ? '#ffffff' : '#333333'} strokeWidth={2.4} strokeLinecap="round" />}
              </g>
            )}
            <text x={x} y={y + r + 22} textAnchor="middle" fontSize={agregado ? 18 : 17} fontWeight={600} fill={agregado ? 'var(--texto)' : 'var(--texto-suave)'}>{nomeNo(n)}</text>
            {rotulos[n] && <text x={x} y={y + r + 40} textAnchor="middle" fontSize="15" fill="var(--texto-suave)" className="num">{rotulos[n]}</text>}
          </g>
        )
      })}
    </svg>
    {hover && resumos?.[hover] && rectNo && createPortal((() => {
      const r = resumos[hover]
      const larg = 260
      const left = Math.max(8, Math.min(rectNo.left + rectNo.width / 2 - larg / 2, window.innerWidth - larg - 8))
      const abaixo = window.innerHeight - rectNo.bottom > 190
      return (
        <div role="tooltip" className="flutua-in pointer-events-none rounded-card bg-sup p-3 text-left shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] ring-1 ring-borda"
          style={{ position: 'fixed', zIndex: 70, width: larg, left, ...(abaixo ? { top: rectNo.bottom + 6 } : { bottom: window.innerHeight - rectNo.top + 6 }) }}>
          <p className="text-[15px] font-bold text-texto">{r.nome}</p>
          <p className="text-[12.5px] text-suave">{r.papel}</p>
          <p className="mt-1.5 text-[13px] font-semibold">{r.status}{r.duracao ? `, ${r.duracao}` : ''}</p>
          <p className="mt-0.5 text-[14px] leading-snug">{r.frase.charAt(0).toUpperCase() + r.frase.slice(1)}</p>
          {r.numeros.length > 0 && (
            <div className="mt-2 flex gap-4 border-t border-borda pt-2">
              {r.numeros.map(([k, v]) => <div key={k}><p className="text-[11.5px] text-suave">{k}</p><p className="text-[14px] font-bold">{v}</p></div>)}
            </div>
          )}
        </div>
      )
    })(), document.body)}
    </div>
  )
}

/** Grafo agregado: volume nas arestas e UMA métrica que importa por nó. */
export function agregarCasos(cases: Case[]) {
  const pesos: Record<string, number> = {}
  const cont = { casos: 0, classificados: 0, dossies: 0, portal: 0, fraudes: 0, decisoes: 0, acoes: 0, aprovacoes: 0, humanas: 0, respostas: 0, padroes: 0 }
  for (const c of cases) {
    for (const a of arestasDoCaso(c)) pesos[`${a.de}>${a.para}`] = (pesos[`${a.de}>${a.para}`] ?? 0) + 1
    const ag = new Set((c.timeline ?? []).filter((s) => s.status !== 'pendente').map((s) => s.agent))
    cont.casos++
    if (ag.has('porteiro')) cont.classificados++
    if (ag.has('rastreador')) cont.dossies++
    if (c.dossie?.fonte_rastreio === 'portal') cont.portal++
    if (c.status === 'rejeitado' || c.veracidade?.fraude_objetiva || c.veracidade?.comparacao) cont.fraudes++
    if (c.decisao?.nivel) cont.decisoes++
    cont.acoes += (c.acoes ?? []).filter((a) => a.agent === 'executor' && a.tipo !== 'leitura').length
    if (c.aprovacao?.aprovacao_id) cont.aprovacoes++
    if ((c.aprovacao?.status && c.aprovacao.status !== 'pendente') || c.responsavel || c.status === 'humano') cont.humanas++
    if (c.resposta?.texto) cont.respostas++
    if ((c.timeline ?? []).some((s) => s.agent === 'analista' && /cluster|padr/i.test(s.titulo))) cont.padroes++
  }
  const arestas: Aresta[] = Object.entries(pesos).map(([k, peso]) => {
    const [de, para] = k.split('>')
    return { de, para, peso, tipo: para === 'humano' ? 'aprovacao' : de === 'humano' ? 'retorno' : 'plano' }
  })
  const n = (v: number, um: string, varios: string) => `${v} ${v === 1 ? um : varios}`
  const rotulos: Record<string, string> = {
    maestro: n(cont.casos, 'caso', 'casos'),
    porteiro: n(cont.classificados, 'classificado', 'classificados'),
    rastreador: cont.portal ? `${n(cont.dossies, 'dossiê', 'dossiês')}, ${cont.portal} no portal` : n(cont.dossies, 'dossiê', 'dossiês'),
    detetive: n(cont.fraudes, 'fraude barrada', 'fraudes barradas'),
    politica: n(cont.decisoes, 'decisão', 'decisões'),
    executor: n(cont.acoes, 'ação feita', 'ações feitas'),
    mensageiro: n(cont.aprovacoes, 'aprovação', 'aprovações'),
    humano: n(cont.humanas, 'decisão humana', 'decisões humanas'),
    voz: n(cont.respostas, 'resposta', 'respostas'),
    analista: n(cont.padroes, 'padrão ligado', 'padrões ligados'),
  }
  return { arestas, rotulos }
}
