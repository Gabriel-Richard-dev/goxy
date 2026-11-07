import { useEffect, useRef, useState } from 'react'
import type { Dict } from '../types'
import { api } from '../api'
import { brl, cx } from '../lib'
import { VOCE } from './Atendimento'
import { NumberTicker } from './magic'

// "Aprende sem se reescrever": concordância humano x Goxy, divergências e sugestões de regra
// validadas nos clientes sintéticos antes de virar nova versão da política.

const STATUS: Record<string, { r: string; c: string }> = {
  proposta: { r: 'Proposta', c: 'bg-sup2 text-suave' },
  validando: { r: 'Validando', c: 'bg-agora-suave text-texto' },
  validada: { r: 'Validada', c: 'bg-ok-suave text-ok' },
  reprovada: { r: 'Reprovada', c: 'bg-critico-suave text-critico' },
  aplicada: { r: 'Aplicada', c: 'bg-ok-suave text-ok' },
}

const fmt = (regra: string, v: unknown) => (typeof v === 'number' && /limite|valor|r\$|reais/i.test(regra) ? brl(v) : String(v))

function regraHumana(s: Dict): string {
  if (s.titulo) return s.titulo
  const regra = String(s.regra ?? '')
  if (s.de != null && s.para != null) {
    const r = regra.toLowerCase()
    const nome = /reenvio/.test(r) ? 'o limite de reenvio do Goxy'
      : /reembolso/.test(r) ? 'o limite de reembolso do Goxy'
      : /troca/.test(r) ? 'o limite de troca do Goxy'
      : /n[ií]vel[_ .]?b|nivel_b/.test(r) ? 'o limite do nível B'
      : /score|veracidade/.test(r) ? 'a nota mínima de veracidade'
      : regra.split('.').pop()!.replace(/_/g, ' ').replace(/\brs\b/i, '').trim()
    return `${s.para > s.de ? 'Subir' : 'Baixar'} ${nome || 'a regra'} de ${fmt(regra, s.de)} para ${fmt(regra, s.para)}`
  }
  return regra.charAt(0).toUpperCase() + regra.slice(1)
}

export function Aprendizado() {
  const [d, setD] = useState<Dict | null>(null)
  const carregar = () => api.get<Dict>('/api/goxys/aprendizado').then(setD).catch(() => {})
  useEffect(() => { carregar(); const t = setInterval(carregar, 10000); return () => clearInterval(t) }, [])
  if (!d) return null
  const pct = Math.round(d.concordancia_pct ?? 0)
  const porNivel = Object.entries((d.por_nivel ?? {}) as Dict)
  const div: Dict[] = d.divergencias ?? []
  const sug: Dict[] = d.sugestoes ?? []
  return (
    <section className="mt-6 rounded-col bg-sup p-7">
      <h2 className="text-[18px] font-bold text-texto">Aprendizado</h2>
      <p className="text-[14px] text-suave">O Goxy aprende com as decisões do time, sem se reescrever: cada regra nova é testada antes de entrar.</p>
      <div className="mt-5 grid gap-8 lg:grid-cols-[1fr_1.4fr]">
        <div>
          <p className="text-[18px] leading-snug">O time concordou com o Goxy em</p>
          <p className="text-[56px] font-extrabold leading-none text-ok"><NumberTicker texto={`${pct}%`} chave="concordancia" /></p>
          <p className="text-[15px] text-suave">{d.total_decisoes === 1 ? 'da 1 decisão' : `das ${d.total_decisoes ?? 0} decisões`}</p>
          {porNivel.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {porNivel.map(([k, v]) => {
                const p = typeof v === 'number' ? v : (v as Dict)?.concordancia_pct
                return <span key={k} className="rounded-full bg-sup2 px-3 py-1 text-[13.5px]"><b>{k}</b> <span className="num">{p != null ? `${Math.round(p)}%` : '—'}</span></span>
              })}
            </div>
          )}
          {div.length > 0 && (
            <div className="mt-5">
              <p className="text-[14.5px] font-semibold">Onde o time discordou</p>
              <ul className="mt-1 space-y-1.5 text-[14px]">
                {div.slice(0, 4).map((x, i) => (
                  <li key={i} className="border-l-2 border-espera pl-3">
                    Goxy recomendou <b>{String(x.recomendado).replace(/_/g, ' ')}</b>, {x.aprovador ?? 'o time'} decidiu <b>{String(x.decidido).replace(/_/g, ' ')}</b>
                    {x.nota && <span className="block text-suave">"{x.nota}"</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="space-y-3">
          {sug.length === 0 && <p className="text-suave">Nenhuma sugestão de regra por enquanto.</p>}
          {sug.map((s) => <CartaoSugestao key={s.id} s={s} onMudou={carregar} />)}
        </div>
      </div>
    </section>
  )
}

function CartaoSugestao({ s, onMudou }: { s: Dict; onMudou: () => void }) {
  const [prog, setProg] = useState<number | null>(null)
  const [placar, setPlacar] = useState<Dict | null>(s.placar ?? null)
  const [confirma, setConfirma] = useState(false)
  const [aplicada, setAplicada] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  useEffect(() => { if (s.placar) setPlacar(s.placar) }, [s.placar])
  const st = STATUS[s.status] ?? STATUS.proposta

  const validar = async () => {
    setErro(null); setProg(0)
    try {
      const r = await api.post<Dict>(`/api/goxys/aprendizado/sugestoes/${s.id}/validar`)
      const job = r?.job
      const passo = async () => {
        const j = await api.get<Dict>(`/api/goxys/aprendizado/jobs/${job}`).catch(() => null)
        const p = j?.progresso ?? 0
        const frac = p > 1 ? p / 100 : p
        setProg(frac)
        if (j?.placar && frac >= 1) { setPlacar(j.placar); setProg(null); onMudou(); return }
        timer.current = window.setTimeout(passo, 700)
      }
      if (job != null) passo(); else { setProg(null); onMudou() }
    } catch (e) { setProg(null); setErro((e as Error).message) }
  }
  const aplicar = async () => {
    setErro(null); setConfirma(false)
    try {
      const r = await api.post<Dict>(`/api/goxys/aprendizado/sugestoes/${s.id}/aplicar`, { por: VOCE })
      setAplicada(r?.politica_versao ?? r?.versao ?? 'nova')
      onMudou()
    } catch (e) { setErro((e as Error).message) }
  }

  return (
    <article className="rounded-card bg-sup2 p-4">
      <div className="flex items-start gap-3">
        <p className="flex-1 text-[16px] font-semibold leading-snug">{regraHumana(s)}</p>
        <span className={cx('shrink-0 rounded-full px-2.5 py-0.5 text-[12.5px] font-semibold', st.c)}>{st.r}</span>
      </div>
      {s.evidencia && <p className="mt-1 text-[14px] text-suave">{typeof s.evidencia === 'string' ? s.evidencia : JSON.stringify(s.evidencia)}</p>}
      {prog != null && (
        <div className="mt-3" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(prog * 100)} aria-label="Validando nos clientes sintéticos">
          <div className="h-2 overflow-hidden rounded-full bg-borda"><div className="h-full rounded-full bg-marca transition-[width] duration-500" style={{ width: `${Math.max(4, prog * 100)}%` }} /></div>
          <p className="mt-1 text-[13px] text-suave">Rodando os 20 clientes sintéticos… <span className="num">{Math.round(prog * 100)}%</span></p>
        </div>
      )}
      {placar && prog == null && (
        <p className="mt-2 text-[14px]">Nos clientes sintéticos: <b className="num">{placar.acertos ?? '?'}/{placar.total ?? 20}</b> certos{placar.antes ? <span className="text-suave"> (antes {placar.antes.acertos}/{placar.antes.total})</span> : null}</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {(s.status === 'proposta' || s.status === 'reprovada') && prog == null && (
          <button onClick={validar} className="rounded-full bg-estrutura px-4 py-2 text-[14px] font-semibold text-white hover:brightness-110">Validar nos 20 clientes sintéticos</button>
        )}
        {s.status === 'validada' && !aplicada && (confirma ? (
          <>
            <span className="text-[14px]">Aplicar esta regra para todos os casos novos?</span>
            <button onClick={aplicar} className="rounded-full bg-marca px-4 py-2 text-[14px] font-semibold text-white">Confirmar</button>
            <button onClick={() => setConfirma(false)} className="text-[14px] font-semibold text-suave">Cancelar</button>
          </>
        ) : (
          <button onClick={() => setConfirma(true)} className="rounded-full bg-marca px-4 py-2 text-[14px] font-semibold text-white hover:brightness-110">Aplicar nova versão da política</button>
        ))}
        {(aplicada || s.status === 'aplicada') && <span className="text-[14.5px] font-semibold text-ok">Política {aplicada ?? s.politica_versao ?? s.versao ?? 'nova'} em uso</span>}
      </div>
      {erro && <p role="alert" className="mt-2 text-[13px] font-medium text-critico">Não deu certo: {erro}</p>}
    </article>
  )
}
