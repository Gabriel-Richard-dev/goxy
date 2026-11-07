import { useEffect, useRef, useState } from 'react'
import type { Case, Dict, Mascote } from '../types'
import { api } from '../api'
import { cx, STATUS, titulo } from '../lib'
import { MascoteEstado } from '../mascot'
import { useCasoExibido } from '../exibicao'
import { arestasDoCaso, estadosDoCaso, Grafo } from './Grafo'
import { NivelBadge } from './ui'
import { frase } from './CasoAoVivo'

const CENAS = [
  { n: 1, estado: 'resolvido', fala: 'Meu copo chegou com a tampa rachada, mandei a foto.', vai: 'O time confere a foto e reenvia sozinho.', nivel: 'A' },
  { n: 2, estado: 'fraude', fala: 'Minha capinha veio rasgada, olha a foto.', vai: 'O Detetive descobre que a foto já foi usada e editada por IA.', nivel: 'D' },
  { n: 3, estado: 'investigando', fala: 'Era pra ter chegado dia 28 e nada.', vai: 'Sem API, o Rastreador entra no portal da TransNorte.', nivel: 'A' },
  { n: 4, estado: 'esperando', fala: 'A mala chegou amassada, quero meu dinheiro de volta.', vai: 'Reembolso alto com foto duvidosa: pede a sua aprovação.', nivel: 'C' },
  { n: 5, estado: 'humano', fala: 'Vou no Procon e no Reclame Aqui se não resolverem.', vai: 'Ameaça jurídica: o time congela e passa para uma pessoa.', nivel: 'D' },
]

export function Simular({ mascote, cases, ultimo, onFechar, onVerCaso }: {
  mascote: Mascote; cases: Record<string, Case>; ultimo: string | null; onFechar: () => void; onVerCaso: (id: string) => void
}) {
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null)
  const [ocupado, setOcupado] = useState<string | null>(null)
  const [texto, setTexto] = useState('')
  const [fila, setFila] = useState<number | null>(null)
  const [foco, setFoco] = useState<string | null>(null)
  const [confirmaLimpar, setConfirmaLimpar] = useState(false)
  const [ra, setRa] = useState<{ aberto: boolean; titulo: string; texto: string }>({ aberto: false, titulo: '', texto: '' })
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.focus()
    api.get<Dict>('/api/goxys/analista').then((a) => setFila(a?.resumo?.fila_aberta ?? null)).catch(() => {})
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onFechar()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onFechar])

  const rodar = async (k: string, path: string, ok: string, body?: unknown) => {
    setOcupado(k); setMsg(null)
    try {
      const r = await api.post<Dict>(path, body)
      if (r?.case_id) setFoco(r.case_id)
      setMsg({ ok: true, t: ok })
      return true
    } catch (e) { setMsg({ ok: false, t: `Não deu certo: ${(e as Error).message}` }); return false } finally { setOcupado(null) }
  }

  const casoFoco = useCasoExibido((foco && cases[foco]) || (ultimo ? cases[ultimo] : null) || null)
  const rodando = casoFoco ? [...casoFoco.timeline].reverse().find((s) => s.status === 'rodando') : undefined

  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-center p-3 sm:p-6">
      <button aria-label="Fechar" className="absolute inset-0 bg-black/50" onClick={onFechar} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Simular contato"
        className="flutua-in rolagem relative grid w-full max-w-[1240px] overflow-y-auto rounded-gaveta bg-fundo shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] outline-none lg:grid-cols-[1fr_440px]">
        <div className="p-6 sm:p-8">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <h2 className="text-[26px] font-extrabold tracking-[-0.01em]">Simular contato</h2>
              <p className="text-[15px] text-suave">Escolha uma cena e veja o time trabalhar ao lado.</p>
            </div>
            <button onClick={onFechar} className="flex h-10 w-10 items-center justify-center rounded-full bg-sup2 hover:bg-borda" aria-label="Fechar">
              <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m4 4 8 8M12 4l-8 8" /></svg>
            </button>
          </div>

          <button disabled={!!ocupado} onClick={() => rodar('todos', '/api/goxys/simular/todos', 'Os 5 casos estão entrando, um depois do outro.')}
            className="mt-5 flex w-full items-center gap-4 rounded-col bg-marca px-6 py-4 text-left text-white transition hover:brightness-110 disabled:opacity-60">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M8 5v14l11-7z" /></svg>
            <span><span className="block text-[18px] font-bold">{ocupado === 'todos' ? 'Disparando…' : 'Rodar a demo completa'}</span><span className="text-[14px] opacity-90">Os 5 casos em sequência, do reenvio automático à ameaça de processo</span></span>
          </button>

          {msg && <p role="status" className={cx('mt-3 rounded-card px-4 py-2.5 text-[14px] font-medium', msg.ok ? 'bg-ok-suave text-ok' : 'bg-critico-suave text-critico')}>{msg.t}</p>}

          <ul className="mt-4 space-y-2">
            {CENAS.map((c) => (
              <li key={c.n}>
                <button disabled={!!ocupado} onClick={() => rodar(`c${c.n}`, `/api/goxys/simular/${c.n}`, `Cena ${c.n} no ar.`)}
                  className="group flex w-full items-center gap-4 rounded-col bg-sup p-3 pr-5 text-left transition hover:ring-2 hover:ring-estrutura disabled:opacity-60">
                  <MascoteEstado estado={c.estado} mascote={mascote} size={76} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[16px] font-medium italic leading-snug">"{c.fala}"</span>
                    <span className="mt-1 flex items-center gap-2 text-[14px] text-suave"><NivelBadge nivel={c.nivel} />{c.vai}</span>
                  </span>
                  <span className="shrink-0 rounded-full bg-sup2 px-4 py-2 text-[14px] font-semibold text-estrutura group-hover:bg-estrutura group-hover:text-white">
                    {ocupado === `c${c.n}` ? 'Enviando…' : 'Simular'}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <form className="mt-5 flex items-end gap-2 rounded-col bg-sup p-2 pl-5" onSubmit={async (e) => {
            e.preventDefault()
            if (!texto.trim()) return
            if (await rodar('livre', '/api/goxys/casos', 'Mensagem enviada ao time.', { canal: 'painel', mensagem: texto.trim() })) setTexto('')
          }}>
            <label className="sr-only" htmlFor="livre">Mensagem do cliente</label>
            <textarea id="livre" rows={1} value={texto} onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement).requestSubmit() } }}
              placeholder="Escreva como o cliente…" className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent py-2.5 text-[16px] outline-none placeholder:text-suave" />
            <button type="submit" disabled={!texto.trim() || !!ocupado} aria-label="Enviar" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-estrutura text-white disabled:opacity-40">
              <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M2 8h11M9 4l4 4-4 4" /></svg>
            </button>
          </form>

          <div className="mt-3">
            {!ra.aberto ? (
              <button onClick={() => setRa({ ...ra, aberto: true })} className="text-[14.5px] font-semibold text-estrutura hover:underline">Nova reclamação no Reclame Aqui</button>
            ) : (
              <form className="space-y-2 rounded-col bg-sup p-4" onSubmit={async (e) => {
                e.preventDefault()
                if (!ra.titulo.trim() || !ra.texto.trim()) return
                if (await rodar('ra', '/api/reclameaqui/simular-nova', 'Reclamação publicada no Reclame Aqui. O Goxy vai pegá-la em instantes.', { titulo: ra.titulo.trim(), texto: ra.texto.trim(), empresa: 'Gocase' })) setRa({ aberto: false, titulo: '', texto: '' })
              }}>
                <p className="text-[15px] font-semibold">Nova reclamação no Reclame Aqui</p>
                <input value={ra.titulo} onChange={(e) => setRa({ ...ra, titulo: e.target.value })} placeholder="Título, como o cliente escreveria" aria-label="Título da reclamação"
                  className="w-full rounded-[14px] border border-borda bg-fundo px-3 py-2 text-[15px] text-texto" />
                <textarea value={ra.texto} onChange={(e) => setRa({ ...ra, texto: e.target.value })} rows={3} placeholder="O que aconteceu" aria-label="Texto da reclamação"
                  className="w-full resize-none rounded-[14px] border border-borda bg-fundo px-3 py-2 text-[15px] text-texto" />
                <div className="flex items-center gap-3">
                  <button type="submit" disabled={!!ocupado || !ra.titulo.trim() || !ra.texto.trim()} className="rounded-full bg-estrutura px-4 py-2 text-[14px] font-semibold text-white disabled:opacity-40">{ocupado === 'ra' ? 'Publicando…' : 'Publicar reclamação'}</button>
                  <button type="button" onClick={() => setRa({ ...ra, aberto: false })} className="text-[14px] font-semibold text-suave hover:text-texto">Cancelar</button>
                </div>
              </form>
            )}
          </div>

          <button disabled={!!ocupado} onClick={() => rodar('fila', '/api/goxys/fila/importar?limite=10', 'Fila real importada. Os contatos entram na fila como novos.')}
            className="mt-3 text-[14.5px] font-semibold text-estrutura hover:underline disabled:opacity-50">
            Importar fila real{fila != null ? ` (${fila} conversas)` : ''}
          </button>

          <button disabled={!!ocupado} onClick={() => rodar('sla', '/api/goxys/demo/sla', 'Prazo de aprovação encurtado para 60 s. Se ninguém decidir, o caso sobe para o supervisor.', { segundos: 60 })}
            className="mt-3 block text-[14.5px] font-semibold text-estrutura hover:underline disabled:opacity-50">
            Encurtar prazo de aprovação para 60 s (demo)
          </button>

          <SaudeCanais />

          <div className="mt-6 border-t border-borda pt-4">
            {confirmaLimpar ? (
              <div className="flex flex-wrap items-center gap-3 rounded-card bg-critico-suave px-4 py-3">
                <p className="flex-1 text-[14.5px] font-medium">Esvaziar a fila e apagar todos os casos da demo?</p>
                <button onClick={async () => { setConfirmaLimpar(false); if (await rodar('limpar', '/api/goxys/demo/limpar', 'Demo limpa. A fila está vazia.')) setFoco(null) }}
                  className="rounded-full bg-critico px-4 py-2 text-[14px] font-semibold text-white">Limpar tudo</button>
                <button onClick={() => setConfirmaLimpar(false)} className="text-[14px] font-semibold text-suave hover:text-texto">Cancelar</button>
              </div>
            ) : (
              <button disabled={!!ocupado} onClick={() => setConfirmaLimpar(true)} className="text-[14.5px] font-semibold text-critico hover:underline disabled:opacity-50">Limpar demo</button>
            )}
          </div>
        </div>

        <aside className="flex flex-col border-t border-borda bg-sup p-6 lg:border-l lg:border-t-0">
          {casoFoco ? (
            <>
              <p className="text-[13.5px] text-suave">Agora no ar</p>
              <h3 className="text-[19px] font-semibold leading-tight">{titulo(casoFoco)}</h3>
              <p className="mt-0.5 text-[14px]"><span className="font-semibold">{STATUS[casoFoco.status]?.rotulo}</span>{rodando && <span className="text-suave">: {frase(rodando)}…</span>}</p>
              <Grafo mascote={mascote} arestas={arestasDoCaso(casoFoco)} estados={estadosDoCaso(casoFoco)} chaveCaso={casoFoco.id} className="mt-2" />
              <button onClick={() => onVerCaso(casoFoco.id)} className="mt-auto self-start rounded-full bg-texto px-5 py-2.5 text-[14.5px] font-semibold text-fundo">Acompanhar este caso</button>
            </>
          ) : (
            <div className="m-auto text-center">
              <MascoteEstado estado="parado" mascote={mascote} size={150} />
              <p className="mt-2 text-[15px] text-suave">Escolha uma cena. O grafo acende aqui.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

function SaudeCanais() {
  const [s, setS] = useState<Dict | null>(null)
  useEffect(() => { api.get<Dict>('/api/goxys/saude').then(setS).catch(() => {}) }, [])
  if (!s) return null
  const itens: [string, boolean][] = [['IA', !!s.ia], ['Slack', !!s.slack], ['Telegram', !!s.telegram]]
  return (
    <p className="mt-4 flex flex-wrap gap-3 text-[12.5px] text-suave">
      Canais: {itens.map(([n, ok]) => <span key={n} className="inline-flex items-center gap-1"><span className={cx('h-1.5 w-1.5 rounded-full', ok ? 'bg-ok' : 'bg-critico')} />{n} {ok ? 'ligado' : 'desligado'}</span>)}
    </p>
  )
}
