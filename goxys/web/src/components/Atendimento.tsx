import { useEffect, useRef, useState } from 'react'
import type { Case, Mascote, Membro, Pessoa } from '../types'
import { api } from '../api'
import { cx, hora, nomeCanal } from '../lib'
import { AvatarAgente } from '../mascot'
import { Botao } from './ui'
import { Miniatura } from '../lightbox'
import { Ancorado } from './Ancorado'
import { fotoDoSku, useProdutos } from '../produtos'

export const VOCE = 'supervisor_01' // "você" na demo

const CARGO_UM: Record<string, string> = { atendente: 'Atendente', supervisor: 'Supervisor', analista: 'Analista' }
export const cargoLegivel = (c?: string) => CARGO_UM[c ?? ''] ?? c ?? ''

let equipeCache: Promise<Membro[]> | null = null
export function carregarEquipe() {
  if (!equipeCache) equipeCache = api.get<Membro[]>('/api/goxys/equipe').catch((e) => { equipeCache = null; throw e })
  return equipeCache
}

export function Iniciais({ nome, size = 28 }: { nome: string; size?: number }) {
  const ini = nome.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('')
  return (
    <span className="inline-flex shrink-0 items-center justify-center rounded-full bg-estrutura font-semibold text-white" style={{ width: size, height: size, fontSize: size * 0.4 }} aria-hidden>
      {ini}
    </span>
  )
}

/** Popover "Passar para…": em portal, sugestão do Goxy no topo, equipe agrupada por cargo, navegação por teclado. */
export function PassarPara({ c, onFechar, onPassado }: { c: Case; onFechar: () => void; onPassado: (p: Pessoa) => void }) {
  const [equipe, setEquipe] = useState<Membro[] | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [busca, setBusca] = useState('')
  const [cursor, setCursor] = useState(0)
  const [escolhido, setEscolhido] = useState<Membro | null>(null)
  const [nota, setNota] = useState('')
  const [enviando, setEnviando] = useState(false)
  const caixa = useRef<HTMLDivElement>(null)
  const ancora = useRef<HTMLElement | null>(null)

  const fechar = () => { onFechar(); setTimeout(() => ancora.current?.querySelector('button')?.focus(), 0) }
  useEffect(() => {
    carregarEquipe().then(setEquipe).catch(() => setErro('Não consegui carregar a equipe.'))
    const fora = (e: MouseEvent) => {
      const t = e.target as Node
      if (caixa.current?.contains(t) || ancora.current?.contains(t)) return
      onFechar()
    }
    const t = setTimeout(() => document.addEventListener('mousedown', fora), 0)
    return () => { clearTimeout(t); document.removeEventListener('mousedown', fora) }
  }, [onFechar])

  const sug = c.sugestao_responsavel
  const GRUPOS: [string, string][] = [['supervisor', 'Supervisão'], ['analista', 'Análise'], ['atendente', 'Atendimento']]
  const q = busca.trim().toLowerCase()
  const filtrados = (equipe ?? []).filter((m) => !q || m.nome.toLowerCase().includes(q) || (m.cargo ?? '').includes(q))
  const grupos = GRUPOS.map(([k, t]) => [t, filtrados.filter((m) => m.cargo === k)] as const).filter(([, l]) => l.length)
  const outros = filtrados.filter((m) => !GRUPOS.some(([k]) => k === m.cargo))
  if (outros.length) grupos.push(['Outros', outros])
  const sugMembro = sug && !q ? ({ ...(equipe?.find((m) => m.colaborador_id === sug.colaborador_id) ?? {}), ...sug } as Membro) : null
  const linear: Membro[] = [...(sugMembro ? [sugMembro] : []), ...grupos.flatMap(([, l]) => l)]
  useEffect(() => { setCursor(0) }, [busca])

  const confirmar = async () => {
    if (!escolhido) return
    setEnviando(true); setErro(null)
    try {
      await api.post(`/api/goxys/casos/${c.id}/passar`, { para: escolhido.colaborador_id, ...(nota.trim() ? { nota: nota.trim() } : {}) })
      onPassado(escolhido)
    } catch (e) { setErro(`Não deu para passar: ${(e as Error).message}`) } finally { setEnviando(false) }
  }

  const tecla = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); escolhido ? setEscolhido(null) : fechar() }
    else if (!escolhido && e.key === 'ArrowDown') { e.preventDefault(); setCursor((x) => Math.min(linear.length - 1, x + 1)) }
    else if (!escolhido && e.key === 'ArrowUp') { e.preventDefault(); setCursor((x) => Math.max(0, x - 1)) }
    else if (!escolhido && e.key === 'Enter' && linear[cursor]) { e.preventDefault(); setEscolhido(linear[cursor]) }
  }
  useEffect(() => { caixa.current?.querySelector('[data-cursor="1"]')?.scrollIntoView({ block: 'nearest' }) }, [cursor])

  const canais = (m: Membro) => (Array.isArray(m.canais_atendidos) ? m.canais_atendidos.join(', ') : m.canais_atendidos ?? '')
  let idx = -1
  const Linha = ({ m, motivo }: { m: Membro; motivo?: string }) => {
    idx++
    const meu = idx
    const ativo = meu === cursor
    return (
      <li>
        <button type="button" data-cursor={ativo ? '1' : undefined} onMouseEnter={() => setCursor(meu)} onClick={() => setEscolhido(m)}
          className={cx('flex w-full items-start gap-3 rounded-[12px] px-3 py-2 text-left', ativo ? 'bg-marca-suave' : '')}>
          <Iniciais nome={m.nome} size={32} />
          <span className="min-w-0 flex-1">
            <span className="block text-[14.5px] font-semibold">{m.nome}{m.colaborador_id === VOCE ? ' (você)' : ''}</span>
            <span className="block text-[12.5px] text-suave">{motivo ?? [cargoLegivel(m.cargo), m.nivel].filter(Boolean).join(' · ')}{!motivo && canais(m) ? ` · ${canais(m)}` : ''}</span>
          </span>
        </button>
      </li>
    )
  }

  return (
      <Ancorado largura={360} alinhar="direita" altura={420} onAncora={(el) => { ancora.current = el }}>
        {(max) => (
          <div ref={caixa} role="dialog" aria-label="Passar o caso" onKeyDown={tecla}
            className="flutua-in flex flex-col rounded-[18px] bg-sup p-3 shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] ring-1 ring-borda" style={{ maxHeight: Math.max(220, Math.min(max, 480)) }}>
            {!escolhido ? (
              <>
                <input autoFocus value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar pessoa da equipe" aria-label="Buscar pessoa da equipe"
                  className="w-full shrink-0 rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[14.5px] text-texto" />
                <div className="rolagem mt-2 min-h-0 flex-1 overflow-y-auto" style={{ maxHeight: 'min(60vh, 420px)' }}>
                  {!equipe && !erro && <p className="px-3 py-4 text-[14px] text-suave">Carregando a equipe…</p>}
                  {sugMembro && (
                    <div className="mb-2">
                      <p className="px-3 pb-1 pt-1 text-[12.5px] font-semibold text-estrutura">Sugestão do Goxy</p>
                      <ul><Linha m={sugMembro} motivo={`${cargoLegivel(sug!.cargo)}${sug!.motivo ? `, porque ${sug!.motivo.replace(/^porque\s+/i, '')}` : ''}`} /></ul>
                    </div>
                  )}
                  {grupos.map(([t, l]) => (
                    <div key={t} className="mb-2">
                      <p className="px-3 pb-1 pt-1 text-[12.5px] font-semibold text-suave">{t} <span className="num font-normal">{l.length}</span></p>
                      <ul>{l.map((m) => <Linha key={m.colaborador_id} m={m} />)}</ul>
                    </div>
                  ))}
                  {equipe && !linear.length && <p className="px-3 py-4 text-[14px] text-suave">Ninguém com esse nome.</p>}
                </div>
              </>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-3 px-1">
                  <Iniciais nome={escolhido.nome} size={32} />
                  <p className="min-w-0 flex-1 text-[14.5px]"><b>{escolhido.nome}</b><span className="block text-[12.5px] text-suave">{cargoLegivel(escolhido.cargo)}</span></p>
                  <button type="button" onClick={() => setEscolhido(null)} className="text-[13px] font-semibold text-estrutura hover:underline">Trocar</button>
                </div>
                <input autoFocus value={nota} onChange={(e) => setNota(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmar() } }}
                  placeholder="Nota para quem recebe (opcional)" aria-label="Nota" className="w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[14px] text-texto" />
                <Botao variante="marca" className="h-10 w-full" disabled={enviando} onClick={confirmar}>{enviando ? 'Passando…' : 'Passar caso'}</Botao>
              </div>
            )}
            {erro && <p role="alert" className="mt-2 px-1 text-[13px] font-medium text-critico">{erro}</p>}
          </div>
        )}
      </Ancorado>
  )
}

type Msg = { autor: string; nome?: string; texto: string; ts?: string }

function mensagensDoCaso(c: Case): Msg[] {
  if (c.mensagens?.length) return c.mensagens
  const l: Msg[] = [{ autor: 'cliente', nome: c.cliente_nome ?? undefined, texto: c.mensagem, ts: c.criado_em }]
  if (c.resposta?.texto) l.push({ autor: 'goxys', texto: c.resposta.texto })
  return l
}

/** Aba "Conversa": balões no estilo do canal; campo só com responsável. */
export function Conversa({ c, mascote }: { c: Case; mascote: Mascote }) {
  const [texto, setTexto] = useState('')
  const [ocupado, setOcupado] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [devolver, setDevolver] = useState(false)
  const fim = useRef<HTMLDivElement>(null)
  const msgs = mensagensDoCaso(c)
  const resp = c.responsavel

  useEffect(() => { fim.current?.scrollIntoView({ block: 'end' }) }, [msgs.length])

  const acao = async (k: string, fn: () => Promise<unknown>) => {
    setOcupado(k); setErro(null)
    try { await fn() } catch (e) { setErro((e as Error).message) } finally { setOcupado(null) }
  }
  const primeiroCliente = msgs.findIndex((m) => m.autor === 'cliente')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={cx('rolagem min-h-0 flex-1 overflow-y-auto p-4', 'bg-sup2')}>
      <ol className="space-y-2">
        {msgs.map((m, i) => {
          const cliente = m.autor === 'cliente'
          const robo = m.autor === 'goxys' || m.autor === 'bot'
          return (
            <li key={i} className={cx('flex items-end gap-2', cliente ? 'justify-start' : 'justify-end')}>
              <div className={cx('max-w-[80%] rounded-[18px] px-4 py-2.5 text-[15px] leading-relaxed shadow-[0_1px_0_rgba(10,22,51,.08)]',
                cliente ? 'rounded-bl-[4px] bg-sup text-texto' : robo ? 'rounded-br-[4px] bg-estrutura text-white' : 'rounded-br-[4px] bg-[#e3ecf8] text-[#333333]')}>
                {!cliente && <span className={cx('mb-0.5 block text-[12.5px] font-semibold', robo ? 'text-white/80' : 'text-[#2659a5]')}>{robo ? 'Goxy' : m.nome ?? resp?.nome ?? 'Atendente'}</span>}
                {cliente && i === primeiroCliente && c.anexos?.length > 0 && (
                  <div className="mb-2 space-y-1.5">
                    {c.anexos.slice(0, 4).map((a, k) => a.url ? (
                      <Miniatura key={a.anexo_id ?? k} src={a.url} alt={a.descricao_visual ?? 'foto do cliente'} legenda="Foto enviada pela cliente" className="h-24 w-24 rounded-[12px] object-cover" />
                    ) : (
                      <div key={a.anexo_id ?? k} className="flex gap-2 rounded-[12px] bg-sup2 px-3 py-2 text-[13.5px]">
                        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" className="mt-0.5 shrink-0 text-suave" aria-hidden><rect x="2" y="3" width="12" height="10" rx="2" /><circle cx="6" cy="7" r="1.3" /><path d="m3 12 3.5-3 2.5 2 2-1.5L13 12" /></svg>
                        <span><span className="font-semibold">Foto {a.anexo_id}</span>{a.descricao_visual ? <span className="block text-suave">"{a.descricao_visual}"</span> : null}</span>
                      </div>
                    ))}
                  </div>
                )}
                <p className="whitespace-pre-line">{m.texto}</p>
                {m.ts && <span className={cx('mt-0.5 block text-right text-[11.5px]', cliente ? 'text-suave' : robo ? 'text-white/70' : 'text-[#666b75]')}>{hora(m.ts).slice(0, 5)}</span>}
              </div>
              {!cliente && (robo ? <AvatarAgente agent="voz" mascote={mascote} size={28} /> : <Iniciais nome={m.nome ?? resp?.nome ?? '?'} size={28} />)}
            </li>
          )
        })}
      </ol>
      <div ref={fim} />
      </div>
      <div className="shrink-0 border-t border-borda bg-sup px-4 pb-4 pt-1">

      {resp ? (
        <form className="mt-4 space-y-2" onSubmit={(e) => {
          e.preventDefault()
          if (!texto.trim()) return
          acao('enviar', async () => { await api.post(`/api/goxys/casos/${c.id}/mensagens`, { texto: texto.trim(), autor: resp.colaborador_id, nome: resp.nome }); setTexto('') })
        }}>
          <div className="flex items-end gap-2 rounded-[22px] bg-sup p-1.5 pl-4 ring-1 ring-borda">
            <textarea value={texto} onChange={(e) => setTexto(e.target.value)} rows={2} aria-label="Mensagem para o cliente" placeholder={`Responder como ${resp.nome.split(' ')[0]}…`}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement).requestSubmit() } }}
              className="min-h-[44px] flex-1 resize-none bg-transparent py-2 text-[15px] outline-none placeholder:text-suave" />
            <button type="submit" disabled={!texto.trim() || !!ocupado} aria-label="Enviar" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-estrutura text-white disabled:opacity-40">
              <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M2 8h11M9 4l4 4-4 4" /></svg>
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Botao type="button" variante="fantasma" disabled={!!ocupado} onClick={() => acao('sugerir', async () => {
              const r = await api.post<{ texto: string }>(`/api/goxys/casos/${c.id}/sugerir-resposta`)
              if (r?.texto) setTexto(r.texto)
            })}>{ocupado === 'sugerir' ? 'A Voz está escrevendo…' : 'Sugerir resposta'}</Botao>
            <span className="relative ml-auto">
              <Botao type="button" disabled={!!ocupado} onClick={() => setDevolver((x) => !x)} aria-expanded={devolver}>Devolver ao Goxy</Botao>
              {devolver && (
                <span className="flutua-in absolute bottom-full right-0 z-20 mb-2 flex w-56 flex-col rounded-card bg-sup p-1.5 shadow-[0_8px_22px_-12px_rgba(0,0,0,.28)] ring-1 ring-borda">
                  {([['seguir', 'Goxy continua'], ['encerrar', 'Encerrar caso']] as const).map(([k, r]) => (
                    <button key={k} type="button" className="rounded-[12px] px-3 py-2 text-left text-[14.5px] font-medium hover:bg-sup2"
                      onClick={() => { setDevolver(false); acao('devolver', () => api.post(`/api/goxys/casos/${c.id}/devolver`, { nota: texto.trim(), instrucao: k })) }}>{r}</button>
                  ))}
                </span>
              )}
            </span>
          </div>
        </form>
      ) : (
        <div className="mt-4 flex justify-center">
          <Botao variante="marca" disabled={!!ocupado} onClick={() => acao('assumir', () => api.post(`/api/goxys/casos/${c.id}/passar`, { para: VOCE }))}>
            {ocupado === 'assumir' ? 'Assumindo…' : 'Assumir conversa'}
          </Botao>
        </div>
      )}
      {erro && <p role="alert" className="mt-2 text-[13px] font-medium text-critico">Não deu certo: {erro}</p>}
      </div>
    </div>
  )
}

/** Modal da conversa: ~640 x 80vh, tela cheia no celular. */
export function ModalConversa({ c, mascote, onFechar }: { c: Case; mascote: Mascote; onFechar: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const produtos = useProdutos()
  const ped = c.dossie?.pedido ?? {}
  const foto = fotoDoSku(produtos, ped.sku ?? c.decisao?.sku)
  useEffect(() => {
    const voltar = document.activeElement as HTMLElement | null
    ref.current?.focus()
    const tecla = (e: KeyboardEvent) => {
      if (document.querySelector('.lightbox-in')) return
      if (e.key === 'Escape') onFechar()
      else if (e.key === 'Tab' && ref.current) {
        const f = [...ref.current.querySelectorAll<HTMLElement>('button, textarea, input, a[href]')].filter((x) => !x.hasAttribute('disabled'))
        if (!f.length) return
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus() }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus() }
      }
    }
    window.addEventListener('keydown', tecla)
    return () => { window.removeEventListener('keydown', tecla); voltar?.focus() }
  }, [onFechar])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center sm:p-6">
      <button aria-label="Fechar conversa" tabIndex={-1} className="absolute inset-0 bg-black/50" onClick={onFechar} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Conversa com o cliente"
        className="flutua-in relative flex h-full w-full flex-col overflow-hidden bg-sup shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] outline-none sm:h-[80vh] sm:max-w-[640px] sm:rounded-[40px]">
        <header className="flex shrink-0 items-center gap-3 border-b border-borda px-5 py-3">
          {foto && <Miniatura src={foto.img} alt={ped.descricao_produto ?? foto.nome} legenda="Foto de catálogo, não da cliente" className="h-11 w-11 rounded-[12px] bg-white object-contain ring-1 ring-borda" />}
          <div className="min-w-0 flex-1">
            <p className="truncate text-[16px] font-bold">{c.cliente_nome ?? 'Cliente'}</p>
            <p className="truncate text-[13.5px] text-suave">{nomeCanal(c.canal)}{c.order_id ? <>, pedido <span className="font-mono text-[12.5px]">{c.order_id}</span></> : null}</p>
          </div>
          <button onClick={onFechar} aria-label="Fechar" className="flex h-10 w-10 items-center justify-center rounded-full bg-sup2 hover:bg-borda">
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </header>
        <div className="min-h-0 flex-1"><Conversa c={c} mascote={mascote} /></div>
      </div>
    </div>
  )
}
