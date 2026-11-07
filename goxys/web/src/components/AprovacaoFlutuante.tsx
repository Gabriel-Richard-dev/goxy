import { useEffect, useRef, useState } from 'react'
import type { Aprovacao, Case, Mascote } from '../types'
import { api, ApiError } from '../api'
import { brl, cx, nomeCanal } from '../lib'
import { MascoteEstado } from '../mascot'
import { Botao } from './ui'

const ehPublica = (a: Aprovacao) => a.acao_proposta === 'publicar_resposta_publica_ra' || /reclame aqui/i.test(a.titulo)
function SeloPublica() {
  return <span className="ml-2 inline-flex translate-y-[-2px] items-center rounded-full bg-critico-suave px-2 py-0.5 align-middle text-[12px] font-bold text-critico" title="A resposta fica visível para todo mundo">Pública</span>
}

/** Título curto: o valor aparece uma vez só, em destaque, fora do título. */
export function tituloCurto(t: string) {
  const x = t.replace(/\s*(de|no valor de)?\s*R\$\s?[\d.]+(,\d{2})?/gi, '').replace(/\s*·\s*/g, ', ').replace(/^[,\s]+|[,\s]+$/g, '').replace(/\s{2,}/g, ' ')
  return x ? x.charAt(0).toUpperCase() + x.slice(1) : t
}

function useAvisoDecisao(lista: Aprovacao[]) {
  const [aviso, setAviso] = useState<Aprovacao | null>(null)
  const vistas = useRef<Set<string> | null>(null)
  useEffect(() => {
    const decididas = lista.filter((x) => x.status === 'decidida')
    if (vistas.current == null) { vistas.current = new Set(decididas.map((x) => x.aprovacao_id)); return }
    const nova = decididas.find((x) => !vistas.current!.has(x.aprovacao_id))
    decididas.forEach((x) => vistas.current!.add(x.aprovacao_id))
    if (nova) { setAviso(nova); const t = setTimeout(() => setAviso(null), 7000); return () => clearTimeout(t) }
  }, [lista])
  return aviso
}

function Aviso({ a }: { a: Aprovacao }) {
  return (
    <div role="status" className="flutua-in pointer-events-auto w-full rounded-card bg-texto px-5 py-3 text-[14.5px] text-fundo shadow-[0_8px_22px_-12px_rgba(0,0,0,.28)]">
      <b>{a.decisao === 'aprovar' ? 'Aprovado' : a.decisao === 'negar' ? 'Negado' : 'Ajustado'}</b> no {nomeCanal(a.canal)}{a.aprovador ? ` por ${a.aprovador}` : ''}. O caso voltou ao fluxo.
    </div>
  )
}

/** Em "Ao vivo": coluna reservada à direita (só existe quando há pendência). */
export function AprovacaoColuna({ lista, cases, mascote, onVerCaso, compacto }: { lista: Aprovacao[]; cases: Record<string, Case>; mascote: Mascote; onVerCaso: (id: string) => void; compacto?: boolean }) {
  const pendentes = lista.filter((a) => a.status !== 'decidida')
  const aviso = useAvisoDecisao(lista)
  if (!pendentes.length && !aviso) return null
  return (
    <aside aria-label="Aprovações" className={cx('flex min-h-0 flex-col gap-3', !compacto && 'rolagem w-full overflow-y-auto pb-3')}>
      {aviso && <Aviso a={aviso} />}
      {pendentes.slice(0, compacto ? 1 : 3).map((a, i) => <Cartao key={a.aprovacao_id} a={a} c={a.case_id ? cases[a.case_id] : undefined} mais={i === 0 ? pendentes.length - 1 : 0} mascote={mascote} onVerCaso={onVerCaso} compacto={compacto} />)}
    </aside>
  )
}

/** Fora de "Ao vivo": só uma pílula pequena no canto. */
export function AprovacaoPilula({ lista, mascote, onAbrir }: { lista: Aprovacao[]; mascote: Mascote; onAbrir: () => void }) {
  const n = lista.filter((a) => a.status !== 'decidida').length
  const aviso = useAvisoDecisao(lista)
  if (!n && !aviso) return null
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-30 flex w-[min(360px,calc(100vw-2rem))] flex-col items-end gap-2">
      {aviso && <Aviso a={aviso} />}
      {n > 0 && (
        <button onClick={onAbrir} className="flutua-in pointer-events-auto flex items-center gap-2 rounded-full bg-sup py-1.5 pl-1.5 pr-4 text-[14.5px] font-semibold shadow-[0_8px_22px_-12px_rgba(0,0,0,.28)] ring-2 ring-espera">
          <MascoteEstado estado="esperando" mascote={mascote} size={34} fundo={false} />
          {n === 1 ? '1 aprovação esperando' : `${n} aprovações esperando`}
        </button>
      )}
    </div>
  )
}

function Cartao({ a, c, mais, mascote, onVerCaso, compacto }: { a: Aprovacao; c?: Case; mais: number; mascote: Mascote; onVerCaso: (id: string) => void; compacto?: boolean }) {
  const [opcoes, setOpcoes] = useState(false)
  const [valor, setValor] = useState(a.valor != null ? String(a.valor) : '')
  const [nota, setNota] = useState('')
  const [enviando, setEnviando] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const resumo = (a.resumo || '').split('\n').map((l) => l.trim()).filter(Boolean)
  const porque = resumo.find((l) => /^por que/i.test(l))?.replace(/^por que precisa de você:\s*/i, '')
  const primeira = resumo[0]

  const decidir = async (decisao: 'aprovar' | 'negar' | 'ajustar') => {
    setEnviando(decisao); setErro(null)
    const body: Record<string, unknown> = { decisao, aprovador: 'Supervisor no painel', canal: 'painel' }
    if (decisao === 'ajustar') {
      const v = Number(valor.replace(',', '.'))
      body.ajuste = { ...(valor && !Number.isNaN(v) ? { valor: v } : {}), ...(nota ? { nota } : {}) }
    }
    try { await api.post(`/api/aprovacoes/${a.aprovacao_id}/decisao`, body) } catch (e) {
      setErro(e instanceof ApiError && e.status === 409 ? 'Alguém já decidiu em outro canal.' : `Não deu para enviar: ${(e as Error).message}`)
    } finally { setEnviando(null) }
  }

  if (compacto && !opcoes) {
    return (
      <article aria-label="Aprovação pendente" className="flutua-in flex w-full flex-wrap items-center gap-x-4 gap-y-2 rounded-col bg-sup py-2 pl-2 pr-4 ring-2 ring-espera">
        <MascoteEstado estado="esperando" mascote={mascote} size={48} fundo={false} />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-espera-texto">O time precisa de você{mais > 0 ? ` (+${mais})` : ''}</p>
          <p className="truncate text-[15.5px] font-bold">{tituloCurto(a.titulo)}{ehPublica(a) && <SeloPublica />}{a.valor ? <span className="num ml-2 text-[17px] font-extrabold">{brl(a.valor)}</span> : null}</p>
        </div>
        <div className="flex items-center gap-2">
          <Botao variante="marca" disabled={!!enviando} onClick={() => decidir('aprovar')}>{enviando === 'aprovar' ? 'Aprovando…' : 'Aprovar'}</Botao>
          <Botao variante="perigo" disabled={!!enviando} onClick={() => decidir('negar')}>{enviando === 'negar' ? 'Negando…' : 'Negar'}</Botao>
          <button onClick={() => setOpcoes(true)} className="px-1 text-[14px] font-semibold text-estrutura hover:underline">Mais opções</button>
          {c && <button onClick={() => onVerCaso(c.id)} className="px-1 text-[14px] font-semibold text-estrutura hover:underline">Ver caso</button>}
        </div>
        {erro && <p role="alert" className="w-full text-[13px] font-medium text-critico">{erro}</p>}
      </article>
    )
  }

  return (
    <article aria-label="Aprovação pendente" className="flutua-in w-full rounded-col bg-sup p-5 ring-2 ring-espera">
      <div className="flex items-start gap-3">
        <MascoteEstado estado="esperando" mascote={mascote} size={64} />
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-semibold text-espera-texto">O time precisa de você{mais > 0 ? ` (+${mais})` : ''}</p>
          <h3 className="text-[17px] font-semibold leading-snug">{tituloCurto(a.titulo)}{ehPublica(a) && <SeloPublica />}</h3>
          {a.valor ? <p className="num mt-0.5 text-[26px] font-extrabold leading-none">{brl(a.valor)}</p> : null}
          {primeira && <p className="text-[13.5px] text-suave">{primeira.replace(/ · /g, ', ')}</p>}
        </div>
      </div>
      {porque && <p className="mt-3 text-[14.5px] leading-snug">{porque.split(/\. Antes:/)[0]}</p>}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Botao variante="marca" disabled={!!enviando} onClick={() => decidir('aprovar')}>{enviando === 'aprovar' ? 'Aprovando…' : 'Aprovar'}</Botao>
        <Botao variante="perigo" disabled={!!enviando} onClick={() => decidir('negar')}>{enviando === 'negar' ? 'Negando…' : 'Negar'}</Botao>
      </div>
      <div className="mt-2 flex items-center justify-between text-[14px]">
        <button onClick={() => setOpcoes((x) => !x)} aria-expanded={opcoes} className="font-semibold text-estrutura hover:underline">Mais opções</button>
        <span className="flex gap-4">
          {c && <button onClick={() => onVerCaso(c.id)} className="font-semibold text-estrutura hover:underline">Ver caso</button>}
        </span>
      </div>
      {opcoes && (
        <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); decidir('ajustar') }}>
          <label className="block text-[13px] font-medium text-suave">Ajustar valor (R$)
            <input inputMode="decimal" value={valor} onChange={(e) => setValor(e.target.value)} className="num mt-1 w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[15px] text-texto" />
          </label>
          <label className="block text-[13px] font-medium text-suave">Nota para o time
            <input value={nota} onChange={(e) => setNota(e.target.value)} placeholder="Ex.: reembolso parcial" className="mt-1 w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[14px] text-texto" />
          </label>
          <Botao type="submit" disabled={!!enviando} className="w-full">{enviando === 'ajustar' ? 'Enviando…' : 'Enviar ajuste'}</Botao>
        </form>
      )}
      {erro && <p role="alert" className={cx('mt-2 text-[13px] font-medium text-critico')}>{erro}</p>}
    </article>
  )
}
