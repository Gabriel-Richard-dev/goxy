import { useEffect, useState, type ReactNode } from 'react'
import { LayoutGroup, motion, useReducedMotion } from 'motion/react'
import type { Aprovacao, Case, Mascote } from '../types'
import { api, ApiError } from '../api'
import { brl, cx, titulo } from '../lib'
import { AvatarAgente, nomeAgente } from '../mascot'
import { IconeCanal, SetaCirculo } from './ui'
import { Miniatura } from '../lightbox'
import { fotoDoSku, useProdutos } from '../produtos'
import { Iniciais, PassarPara } from './Atendimento'
import { Ancorado } from './Ancorado'

type Col = 'novo' | 'trabalhando' | 'voce' | 'pessoa' | 'resolvido' | 'barrado'
const COLUNAS: { k: Col; t: string; cor: string }[] = [
  { k: 'novo', t: 'Novo', cor: '#5f6670' },
  { k: 'trabalhando', t: 'Agentes trabalhando', cor: '#3dbfef' },
  { k: 'voce', t: 'Precisa de você', cor: '#f8ae13' },
  { k: 'pessoa', t: 'Com uma pessoa', cor: '#3dbfef' },
  { k: 'resolvido', t: 'Resolvido', cor: '#2659a5' },
  { k: 'barrado', t: 'Barrado', cor: '#e5273c' },
]
const CURTO: Partial<Record<Col, string>> = { trabalhando: 'Trabalhando', voce: 'Para você', pessoa: 'Com pessoa' }
const TRILHA = ['porteiro', 'rastreador', 'detetive', 'analista', 'mensageiro', 'executor', 'voz']

export function colunaDo(c: Case): Col {
  if (c.status === 'aguardando_aprovacao') return 'voce'
  if (c.status === 'humano' || c.responsavel) return 'pessoa'
  if (c.status === 'resolvido') return 'resolvido'
  if (c.status === 'rejeitado') return 'barrado'
  if (c.status === 'novo') return 'novo'
  return 'trabalhando'
}

function desde(iso?: string | null) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'agora'
  const m = Math.floor(s / 60)
  if (m < 60) return `há ${m} min`
  const h = Math.floor(m / 60)
  return h < 24 ? `há ${h} h` : `há ${Math.floor(h / 24)} d`
}

export function Board({ cases, aprovacoes, mascote, onAbrir, alternador }: { cases: Case[]; aprovacoes: Aprovacao[]; mascote: Mascote; onAbrir: (id: string) => void; alternador?: ReactNode }) {
  const [arrastando, setArrastando] = useState<string | null>(null)
  const [sobre, setSobre] = useState<Col | null>(null)
  const [dica, setDica] = useState<string | null>(null)
  const [confirmar, setConfirmar] = useState<{ c: Case; decisao: 'aprovar' | 'negar' } | null>(null)
  const [passar, setPassar] = useState<string | null>(null)
  const [abaMovel, setAbaMovel] = useState<Col>('voce')
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 15000); return () => clearInterval(t) }, [])
  useEffect(() => { if (!dica) return; const t = setTimeout(() => setDica(null), 2800); return () => clearTimeout(t) }, [dica])

  const pendenteDe = (id: string) => aprovacoes.find((a) => a.case_id === id && a.status !== 'decidida')

  /** Regras de soltar: só onde há efeito real. */
  const mover = (c: Case, destino: Col) => {
    const origem = colunaDo(c)
    if (origem === destino) return
    if (destino === 'pessoa') { setPassar(c.id); return }
    if (origem === 'voce' && pendenteDe(c.id) && (destino === 'resolvido' || destino === 'barrado')) {
      setConfirmar({ c, decisao: destino === 'resolvido' ? 'aprovar' : 'negar' }); return
    }
    setDica('Esse movimento não muda o caso. Use Aprovar, Negar ou Passar para…')
  }

  const porCol = (k: Col) => cases.filter((c) => colunaDo(c) === k)
  const nVoce = porCol('voce').length, nPessoa = porCol('pessoa').length
  const hoje = new Date().toISOString().slice(0, 10)
  const nResolvidos = porCol('resolvido').filter((c) => (c.encerrado_em ?? c.atualizado_em ?? '').slice(0, 10) === hoje || true).length
  const maisAntiga = aprovacoes.filter((a) => a.status !== 'decidida').map((a) => a.pedido_em).filter(Boolean).sort()[0]

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* resumo + alternador */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 px-5 pb-3 pt-3">
        <p className="text-[14.5px]">
          <b className={nVoce ? 'text-[#333333]' : ''}>{nVoce} {nVoce === 1 ? 'precisa' : 'precisam'} de você</b>
          <span className="text-suave">, {nPessoa} com uma pessoa, {nResolvidos} {nResolvidos === 1 ? 'resolvido' : 'resolvidos'} hoje</span>
          {maisAntiga && <span className="ml-2 text-[13.5px] font-semibold text-[#a8710a] dark:text-laranja">pendência mais antiga {desde(maisAntiga)}</span>}
        </p>
        <div className="ml-auto">{alternador}</div>
      </div>

      {/* celular: abas por coluna */}
      <div role="tablist" aria-label="Colunas" className="flex gap-1 overflow-x-auto px-3 pb-2 md:hidden">
        {COLUNAS.map((col) => (
          <button key={col.k} role="tab" aria-selected={abaMovel === col.k} onClick={() => setAbaMovel(col.k)}
            className={cx('shrink-0 rounded-full px-3 py-1.5 text-[13.5px] font-semibold', abaMovel === col.k ? 'bg-azul text-white' : 'bg-sup text-suave')}>
            {col.t} <span className="num">{porCol(col.k).length}</span>
          </button>
        ))}
      </div>

      <div className="rolagem min-h-0 flex-1 overflow-x-auto px-5 pb-4 pt-1">
        <LayoutGroup>
          <div className="flex h-full min-w-0 gap-2.5 md:min-w-[1060px] min-[1100px]:min-w-0">
            {COLUNAS.map((col) => {
              const lista = porCol(col.k)
              const vazia = lista.length === 0
              const forte = col.k === 'voce' && !vazia
              return (
                <section key={col.k} aria-label={`${col.t}, ${lista.length}`}
                  onDragOver={(e) => { if (arrastando) { e.preventDefault(); setSobre(col.k) } }}
                  onDragLeave={() => setSobre((x) => (x === col.k ? null : x))}
                  onDrop={(e) => { e.preventDefault(); const c = cases.find((x) => x.id === arrastando); setSobre(null); setArrastando(null); if (c) mover(c, col.k) }}
                  className={cx('min-h-0 flex-col rounded-[18px] bg-[#ebebeb] transition-[flex-basis] duration-300 dark:bg-sup2 md:flex',
                    abaMovel === col.k ? 'flex w-full' : 'hidden',
                    vazia ? 'md:w-14 md:flex-none' : 'md:min-w-[200px] md:flex-1',
                    forte && 'ring-2 ring-laranja', sobre === col.k && 'ring-2 ring-azul')}>
                  {vazia ? (
                    <>
                      {/* faixa estreita no desktop */}
                      <div className="hidden h-full flex-col items-center gap-2 py-3 md:flex">
                        <span className="h-2 w-2 rounded-full" style={{ background: col.cor }} />
                        <span className="num text-[12.5px] text-suave">0</span>
                        <span title={col.t} className="mt-1 whitespace-nowrap text-[13px] font-semibold text-suave [writing-mode:vertical-rl]">{CURTO[col.k] ?? col.t}</span>
                      </div>
                      {/* aba no celular */}
                      <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-center md:hidden">
                        <img src="/assets-goxys/goxys/base.webp" alt="" className="h-16 w-16 object-contain opacity-40 grayscale" />
                        <p className="text-[13.5px] text-suave">Nenhum caso aqui.</p>
                      </div>
                    </>
                  ) : (
                    <>
                      <header className="flex items-center gap-2 px-3 pb-2 pt-3">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: col.cor }} />
                        <h2 className={cx('flex-1 text-[13.5px] text-texto', forte ? 'font-bold' : 'font-semibold')}>{col.t}</h2>
                        <span className="num text-[13px] text-suave">{lista.length}</span>
                      </header>
                      <ul className="rolagem min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-6" style={{ maskImage: 'linear-gradient(to bottom, black calc(100% - 28px), transparent)', WebkitMaskImage: 'linear-gradient(to bottom, black calc(100% - 28px), transparent)' }}>
                          {lista.map((c) => (
                            <Cartao key={c.id} c={c} col={col.k} mascote={mascote} pendente={pendenteDe(c.id)}
                              onAbrir={() => onAbrir(c.id)} onMover={(d) => mover(c, d)}
                              onDragStart={() => setArrastando(c.id)} onDragEnd={() => { setArrastando(null); setSobre(null) }}
                              passando={passar === c.id} onFecharPassar={() => setPassar(null)} />
                          ))}
                      </ul>
                    </>
                  )}
                </section>
              )
            })}
          </div>
        </LayoutGroup>
      </div>

      {dica && <p role="status" className="flutua-in mx-auto mb-3 rounded-full bg-texto px-4 py-2 text-[14px] font-medium text-fundo">{dica}</p>}
      {confirmar && <Confirmar c={confirmar.c} decisao={confirmar.decisao} aprovacao={pendenteDe(confirmar.c.id)} onFechar={() => setConfirmar(null)} />}
    </div>
  )
}

function IconeCategoria({ c }: { c: Case }) {
  const ped = c.dossie?.pedido ?? {}
  const beleza = /gobeaut|perfum|batom|maquiag|cosm|skin|lescent/i.test(`${ped.marca ?? ''} ${ped.descricao_produto ?? ''}`)
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-[#eef1f6] text-azul" title={ped.descricao_produto ?? 'produto'}>
      <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {beleza ? <path d="M6 1.5h4v2H6zM5 3.5h6l.5 2v8a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1v-8z" /> : <path d="M2 5l6-3 6 3v6l-6 3-6-3zM2 5l6 3 6-3M8 8v6" />}
      </svg>
    </span>
  )
}

function Cartao({ c, col, mascote, pendente, onAbrir, onMover, onDragStart, onDragEnd, passando, onFecharPassar }: {
  c: Case; col: Col; mascote: Mascote; pendente?: Aprovacao; onAbrir: () => void; onMover: (d: Col) => void
  onDragStart: () => void; onDragEnd: () => void; passando: boolean; onFecharPassar: () => void
}) {
  const reduzir = useReducedMotion()
  const produtos = useProdutos()
  const ped = c.dossie?.pedido ?? {}
  const foto = fotoDoSku(produtos, ped.sku ?? c.decisao?.sku)
  const valor = c.decisao?.valor ?? ped.valor
  const porque = c.decisao?.porque ?? (c.decisao?.motivo ? String(c.decisao.motivo).split(/[.;]/)[0] : null)
  const [menu, setMenu] = useState(false)
  const [enviando, setEnviando] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)

  // trilha de agentes: acesa no que já rodou, pulsando no atual
  const st: Record<string, string> = {}
  for (const s of c.timeline ?? []) st[s.agent] = s.status
  const trilha = TRILHA.filter((a) => a in st || ['porteiro', 'rastreador', 'detetive', 'voz'].includes(a)).slice(0, 7)

  // tempo de espera: âmbar perto do prazo, carmim depois
  const expira = pendente?.expira_em ? new Date(pendente.expira_em).getTime() : null
  const resta = expira ? (expira - Date.now()) / 1000 : null
  const corTempo = resta == null ? '' : resta < 0 ? 'rounded-full bg-carmim px-1.5 font-semibold text-white' : resta < 180 ? 'rounded-full bg-laranja px-1.5 font-semibold text-[#333333]' : ''

  const decidir = async (decisao: 'aprovar' | 'negar') => {
    if (!pendente) return
    setEnviando(decisao); setErro(null)
    try { await api.post(`/api/aprovacoes/${pendente.aprovacao_id}/decisao`, { decisao, aprovador: 'Supervisor no painel', canal: 'painel' }) }
    catch (e) { const b = (e instanceof ApiError ? e.body : null) as Record<string, string> | null; setErro(b?.mensagem ?? (e as Error).message) }
    finally { setEnviando(null) }
  }

  return (
    <motion.li layoutId={reduzir ? undefined : c.id} initial={false}
      transition={{ layout: { duration: 0.22, ease: 'easeOut' } }}
      className="relative rounded-card bg-sup p-3 ring-1 ring-transparent hover:ring-azul">
      <div draggable onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; onDragStart() }} onDragEnd={onDragEnd}
        role="button" tabIndex={0} onClick={onAbrir}
        onKeyDown={(e) => { if (e.key === 'Enter') onAbrir(); if (e.key.toLowerCase() === 'm') { e.preventDefault(); setMenu(true) } }}
        aria-label={`${titulo(c)}, ${c.cliente_nome ?? ''}. Enter abre o caso, M move.`} className="block cursor-grab text-left active:cursor-grabbing">
        <div className="flex items-start gap-2">
          {foto ? <span onClick={(e) => e.stopPropagation()}><Miniatura src={foto.img} alt={ped.descricao_produto ?? foto.nome} legenda="Foto de catálogo, não da cliente" className="h-10 w-10 rounded-[10px] bg-white object-contain ring-1 ring-borda" /></span> : <IconeCategoria c={c} />}
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-[14px] font-semibold leading-tight">{titulo(c)}</p>
            <p className="mt-0.5 flex items-center gap-1 truncate text-[12px] text-suave">
              <IconeCanal canal={c.canal} size={12} />
              <span className="truncate">{c.cliente_nome?.split(' ')[0] ?? 'Cliente'}{c.order_id ? <>, <span className="font-mono text-[11.5px]">{c.order_id}</span></> : null}</span>
            </p>
          </div>
          {c.decisao?.nivel && (
            <span title={`Nível ${c.decisao.nivel}`} className={cx('flex h-6 w-6 shrink-0 items-center justify-center rounded-[8px] text-[12.5px] font-extrabold', c.decisao.nivel === 'D' ? 'bg-carmim text-white' : 'text-texto ring-1 ring-borda')}>{c.decisao.nivel}</span>
          )}
        </div>
        {porque && <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-snug text-suave" title={porque}>{porque}</p>}
        <div className="mt-2 flex items-center gap-1.5">
          {(() => {
            const atual = [...(c.timeline ?? [])].reverse().find((x) => x.status === 'rodando') ?? [...(c.timeline ?? [])].reverse().find((x) => x.agent !== 'maestro')
            return (
              <span className="flex items-center gap-1.5" aria-label={`Progresso dos agentes${atual ? `, agora: ${nomeAgente(atual.agent)}` : ''}`}>
                <span className="flex items-center gap-[3px]">
                  {trilha.map((a) => {
                    const v = st[a]
                    const feito = v && !['pendente', 'pulado', 'rodando'].includes(v)
                    return <span key={a} title={nomeAgente(a)} className={cx('h-1.5 w-1.5 rounded-full', v === 'rodando' ? cx('bg-ciano', col === 'trabalhando' && 'ao-vivo') : feito ? 'bg-azul' : 'bg-[#cfd4dc]')} />
                  })}
                </span>
                {atual && <AvatarAgente agent={atual.agent} mascote={mascote} size={20} ativo={col === 'trabalhando' && atual.status === 'rodando'} />}
              </span>
            )
          })()}
          <span className={cx('ml-auto text-[12px] text-suave', corTempo)}>{desde(pendente?.pedido_em || c.criado_em)}</span>
          {valor ? <span className="num text-[12.5px] font-semibold">{brl(valor)}</span> : null}
          {c.responsavel && <span title={c.responsavel.nome}><Iniciais nome={c.responsavel.nome} size={20} /></span>}
        </div>
      </div>
      {col === 'voce' && pendente && (
        <div className="mt-2">
          <div className="flex gap-1.5">
            <button disabled={!!enviando} onClick={() => decidir('aprovar')} className="h-8 flex-1 rounded-full bg-azul text-[13px] font-semibold text-white disabled:opacity-50">{enviando === 'aprovar' ? '…' : 'Aprovar'}</button>
            <button disabled={!!enviando} onClick={() => decidir('negar')} className="h-8 flex-1 rounded-full border border-borda text-[13px] font-semibold text-critico disabled:opacity-50">{enviando === 'negar' ? '…' : 'Negar'}</button>
          </div>
          <div className="mt-1 flex gap-3 px-1 text-[12.5px]">
            <button onClick={onAbrir} className="font-semibold text-estrutura hover:underline">Ajustar</button>
            <button onClick={onAbrir} className="inline-flex items-center gap-1 font-semibold text-estrutura hover:underline">Ver caso<SetaCirculo size={18} /></button>
          </div>
        </div>
      )}
      {erro && <p role="alert" className="mt-1 text-[12.5px] font-medium text-critico">{erro}</p>}
      <button onClick={() => setMenu((x) => !x)} aria-expanded={menu} className="sr-only focus:not-sr-only focus:absolute focus:bottom-1 focus:right-1 focus:rounded-full focus:bg-sup2 focus:px-2 focus:text-[12px]">Mover</button>
      {menu && (
        <Ancorado largura={240} alinhar="esquerda" altura={260}>{() => (
        <div className="rounded-card bg-sup p-1.5 shadow-[0_8px_22px_-12px_rgba(0,0,0,.28)] ring-1 ring-borda" role="menu"
          onKeyDown={(e) => { if (e.key === 'Escape') setMenu(false) }}>
          {COLUNAS.filter((k) => k.k !== col).map((k) => (
            <button key={k.k} role="menuitem" onClick={() => { setMenu(false); onMover(k.k) }} className="block w-full rounded-[10px] px-3 py-1.5 text-left text-[13.5px] hover:bg-sup2">Mover para {k.t}</button>
          ))}
        </div>
        )}</Ancorado>
      )}
      {passando && <PassarPara c={c} onFechar={onFecharPassar} onPassado={onFecharPassar} />}
    </motion.li>
  )
}

function Confirmar({ c, decisao, aprovacao, onFechar }: { c: Case; decisao: 'aprovar' | 'negar'; aprovacao?: Aprovacao; onFechar: () => void }) {
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  useEffect(() => { const t = (e: KeyboardEvent) => e.key === 'Escape' && onFechar(); window.addEventListener('keydown', t); return () => window.removeEventListener('keydown', t) }, [onFechar])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button aria-label="Cancelar" className="absolute inset-0 bg-black/50" onClick={onFechar} />
      <div role="alertdialog" aria-modal="true" aria-label="Confirmar decisão" className="flutua-in relative w-full max-w-[420px] rounded-[40px] bg-sup p-7">
        <p className="text-[18px] font-extrabold">{decisao === 'aprovar' ? 'Aprovar' : 'Negar'} {titulo(c).toLowerCase()}{aprovacao?.valor ? ` de ${brl(aprovacao.valor)}` : ''}?</p>
        <p className="mt-1 text-[14.5px] text-suave">{c.cliente_nome}. A decisão vale como se fosse dada no Slack ou no Telegram.</p>
        {erro && <p role="alert" className="mt-2 text-[13.5px] font-medium text-critico">{erro}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onFechar} className="rounded-full px-4 py-2 text-[14.5px] font-semibold text-suave">Cancelar</button>
          <button autoFocus disabled={enviando} onClick={async () => {
            if (!aprovacao) return onFechar()
            setEnviando(true)
            try { await api.post(`/api/aprovacoes/${aprovacao.aprovacao_id}/decisao`, { decisao, aprovador: 'Supervisor no painel', canal: 'painel' }); onFechar() }
            catch (e) { const b = (e instanceof ApiError ? e.body : null) as Record<string, string> | null; setErro(b?.mensagem ?? (e as Error).message); setEnviando(false) }
          }} className={cx('rounded-full px-5 py-2 text-[14.5px] font-semibold text-white', decisao === 'aprovar' ? 'bg-azul' : 'bg-carmim')}>{enviando ? 'Enviando…' : decisao === 'aprovar' ? 'Aprovar' : 'Negar'}</button>
        </div>
      </div>
    </div>
  )
}
