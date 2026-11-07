import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Case, Mascote, TimelineStep } from '../types'
import { arestasDoCaso, estadosDoCaso, Grafo } from './Grafo'
import { fotoDoSku, useProdutos } from '../produtos'
import { LinkReclameAqui } from './ReclameAqui'
import { Iniciais, ModalConversa } from './Atendimento'
import { cx, dataHora, hora, NIVEL, nomeCanal, titulo } from '../lib'
import { AvatarAgente, MascoteEstado, nomeAgente } from '../mascot'
import { IconeCanal, IconeStatus, Mono } from './ui'
import { CartaoEvidencia, extrairComparacao, mascararCliente } from './Comparacao'
import { GavetaAgente, resumoAgente, ORDEM_GRAFO } from './GavetaAgente'
import { Miniatura } from '../lightbox'

export function usouPortal(c: Case) {
  return c.dossie?.fonte_rastreio === 'portal' || c.acoes?.some((a) => a.ferramenta === 'portal_tracking')
}

/** Título técnico do passo vira frase simples. */
export function frase(s: TimelineStep): string {
  if (s.status === 'pendente') return 'na vez'
  let t = (s.titulo || '').split(' · ')[0].trim()
  if (!t) return s.status === 'rodando' ? 'trabalhando' : ''
  if (s.agent === 'maestro') {
    if (/^plano/i.test(t)) return 'montou o plano do caso'
    const n = t.match(/^n[íi]vel\s+([A-D])/i)
    if (n) return `decidiu: ${NIVEL[n[1].toUpperCase()]?.desc.toLowerCase() ?? `nível ${n[1]}`}`
    if (/^revisor/i.test(t)) return 'conferiu a decisão com o revisor'
  }
  t = t.replace(/\s*\(\w+\)$/, '').replace(/→/g, 'e')
  return t.charAt(0).toLowerCase() + t.slice(1)
}

export function acaoLegivel(a?: string) {
  const m: Record<string, string> = {
    reenvio: 'Reenviar o produto', troca: 'Trocar o produto', reembolso: 'Reembolsar', rejeitar: 'Recusar o pedido', negar: 'Recusar o pedido',
    chamado_transportadora: 'Abrir chamado na transportadora', humano_assume: 'Passar para uma pessoa', nova_producao: 'Produzir de novo',
    publicar_resposta_publica_ra: 'Publicar a resposta no Reclame Aqui',
  }
  return m[a ?? ''] ?? (a ? a.charAt(0).toUpperCase() + a.slice(1).replace(/_/g, ' ') : 'Decisão')
}

type Secao = 'aconteceu' | 'pericia'

/** Centro do "Ao vivo": o grafo é o protagonista; o resto fica em accordions. */
export function CasoAoVivo({ c, mascote, onDetalhe }: { c: Case | null; mascote: Mascote; onDetalhe: () => void }) {
  const arestas = useMemo(() => (c ? arestasDoCaso(c) : []), [c])
  const estados = useMemo(() => (c ? estadosDoCaso(c) : {}), [c])
  const par = c ? extrairComparacao(c) : null
  const [aberto, setAberto] = useState<Record<Secao, boolean>>({ aconteceu: false, pericia: false })
  const [conversa, setConversa] = useState(false)
  // badge "nova": mensagens que chegaram com o modal fechado
  const total = c ? Math.max(c.mensagens?.length ?? 0, 1 + (c.resposta?.texto ? 1 : 0)) : 0
  const nMsgsRef = useRef(total)
  nMsgsRef.current = total
  const lidas = useRef(total)
  useEffect(() => { lidas.current = nMsgsRef.current; setConversa(false) }, [c?.id])
  const novas = !conversa && total > lidas.current
  const abrirConversa = () => { lidas.current = total; setConversa(true) }
  const [foco, setFoco] = useState<string | null>(null)
  const produtos = useProdutos()
  const refAconteceu = useRef<HTMLDivElement>(null)

  // troca de caso: tudo fechado, só a perícia abre sozinha quando existe
  const temPericia = !!par
  useEffect(() => { setAberto({ aconteceu: false, pericia: temPericia }); setFoco(null) }, [c?.id])
  useEffect(() => { if (temPericia) setAberto((a) => ({ ...a, pericia: true })) }, [temPericia])

  if (!c) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <MascoteEstado estado="parado" mascote={mascote} size={180} />
        <p className="text-[18px] font-semibold">Nenhum caso esperando. O time está em dia.</p>
        <p className="text-[15px] text-suave">Use "Simular contato" no topo para ver o time trabalhar.</p>
      </div>
    )
  }
  const rodando = [...c.timeline].reverse().find((s) => s.status === 'rodando')
  const ped = c.dossie?.pedido ?? {}
  const foto = fotoDoSku(produtos, ped.sku ?? c.decisao?.sku)
  const porque = c.decisao?.porque ?? (c.decisao?.motivo ? String(c.decisao.motivo).split(/[:.;]/)[0] : null)
  const passos = c.timeline ?? []
  const nMsgs = total

  const abrirNo = (n: string) => setFoco(n)
  const resumos = Object.fromEntries(ORDEM_GRAFO.map((n) => [n, resumoAgente(c, n)]))

  return (
    <div className="rolagem h-full overflow-y-auto">
      <div className="px-5 pb-10 pt-4">
        {/* título leve sobre o fundo */}
        <div className="flex items-start gap-3">
          {foto && <span className="mt-0.5"><Miniatura src={foto.img} alt={ped.descricao_produto ?? foto.nome} legenda="Foto de catálogo, não da cliente" className="h-10 w-10 rounded-[10px] bg-white object-contain ring-1 ring-borda" /></span>}
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[23px] font-bold leading-tight text-texto">{titulo(c)}</h1>
            <p className="mt-0.5 flex items-center gap-1.5 truncate text-[13.5px] text-suave">
              <IconeCanal canal={c.canal} size={13} />
              {[nomeCanal(c.canal), c.cliente_nome, hora(c.criado_em).slice(0, 5), c.order_id].filter(Boolean).join(' · ')}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <LinkReclameAqui c={c} />
            <button onClick={abrirConversa} className="relative inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[13.5px] font-semibold text-texto ring-1 ring-borda hover:ring-azul" aria-label={`Conversa, ${nMsgs} mensagens${novas ? ', há mensagem nova' : ''}`}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>
              Conversa <span className="num text-suave">{nMsgs}</span>
              {novas && <span className="absolute -right-1 -top-1 rounded-full bg-rosa px-1.5 text-[11px] font-bold leading-[18px] text-white">nova</span>}
            </button>
            <button onClick={onDetalhe} className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[13.5px] font-semibold text-texto ring-1 ring-borda hover:ring-azul">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden><circle cx="8" cy="8" r="6" /><path d="M8 7.2v4M8 4.8v.1" /></svg>
              Ver detalhes
            </button>
          </div>
        </div>

        {/* o grafo, protagonista */}
        <section aria-label="O time neste caso" className="mt-4 rounded-col bg-sup px-4 pb-2 pt-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[13px] text-suave">Maestro coordena</p>
            <p className="truncate text-[13px] text-suave" aria-live="polite">
              {rodando ? <><b className="text-texto">{nomeAgente(rodando.agent)}</b> {frase(rodando)}…</> : 'Toque num agente para ver o que ele fez'}
            </p>
          </div>
          <Grafo mascote={mascote} arestas={arestas} estados={estados} chaveCaso={c.id} selecionado={foco} onNo={abrirNo} resumos={resumos} className="max-h-[420px]" />
        </section>

        {porque && (
          <p className="mt-4 px-1 text-[15px] leading-snug text-texto">{porque.replace(/\.?$/, '.')}</p>
        )}

        <div className="mt-4 space-y-2">
          <div ref={refAconteceu}>
            <Acordeao titulo="O que aconteceu" extra={`${passos.filter((s) => s.status !== 'pendente').length} passos`} aberto={aberto.aconteceu} onToggle={() => setAberto((a) => ({ ...a, aconteceu: !a.aconteceu }))}>
              <ol className="space-y-1">
                {passos.map((s, i) => <PassoCompacto key={`${s.agent}-${i}`} s={s} c={c} mascote={mascote} destaque={false} />)}
              </ol>
            </Acordeao>
          </div>
          {par && (
            <Acordeao titulo="Perícia da foto" extra={par.mesmoHash ? 'mesma foto encontrada' : undefined} alerta={par.mesmoHash} aberto={aberto.pericia} onToggle={() => setAberto((a) => ({ ...a, pericia: !a.pericia }))}>
              <Pericia c={c} par={par} />
            </Acordeao>
          )}
          {conversa && <ModalConversa c={c} mascote={mascote} onFechar={() => { setConversa(false); lidas.current = nMsgsRef.current }} />}
          {foco && <GavetaAgente c={c} no={foco} arestas={arestas} mascote={mascote} onTrocar={setFoco} onFechar={() => setFoco(null)} />}
        </div>
      </div>
    </div>
  )
}

function Acordeao({ titulo: t, extra, alerta, aberto, onToggle, children }: { titulo: string; extra?: string; alerta?: boolean; aberto: boolean; onToggle: () => void; children: ReactNode }) {
  return (
    <section className="rounded-card border border-borda">
      <button onClick={onToggle} aria-expanded={aberto} className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <svg width="16" height="16" viewBox="0 0 16 16" className={cx('shrink-0 text-suave transition-transform', aberto && 'rotate-90')} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="m6 4 4 4-4 4" /></svg>
        <span className="text-[14.5px] font-semibold text-texto">{t}</span>
        {extra && <span className={cx('text-[13.5px]', alerta ? 'font-semibold text-critico' : 'text-suave')}>{extra}</span>}
      </button>
      {aberto && <div className="px-4 pb-4">{children}</div>}
    </section>
  )
}

function PassoCompacto({ s, c, mascote, destaque }: { s: TimelineStep; c: Case; mascote: Mascote; destaque: boolean }) {
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => { if (destaque) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [destaque])
  const pendente = s.status === 'pendente' || s.status === 'pulado'
  const feito = ['ok', 'alerta', 'falhou'].includes(s.status)
  return (
    <li ref={ref} className={cx('rounded-[12px] px-2 py-1.5', destaque && 'bg-sup2 ring-2 ring-estrutura', pendente && 'opacity-50')}>
      <div className="flex items-center gap-2.5">
        <AvatarAgente agent={s.agent} mascote={mascote} size={28} ativo={s.status === 'rodando'} />
        <p className="min-w-0 flex-1 text-[14.5px] leading-snug">
          <b>{nomeAgente(s.agent)}</b> <span className="text-suave">{frase(s)}{s.status === 'rodando' && '…'}</span>
        </p>
        {feito && <span className={cx('flex h-6 w-6 shrink-0 items-center justify-center rounded-full', s.status === 'ok' ? 'bg-ok-suave text-ok' : s.status === 'alerta' ? 'bg-espera-suave text-espera-texto' : 'bg-critico-suave text-critico')}><IconeStatus status={s.status} size={13} /></span>}
      </div>
      {destaque && s.detalhe && <p className="ml-[38px] mt-1 text-[13.5px] text-suave">{s.detalhe}</p>}
      {s.agent === 'rastreador' && feito && usouPortal(c) && <div className="ml-[38px]"><AvisoPortal c={c} /></div>}
    </li>
  )
}

/** Perícia em versão leve: cartões de evidência reais + miniatura do produto comprado. */
function Pericia({ c, par }: { c: Case; par: NonNullable<ReturnType<typeof extrairComparacao>> }) {
  const produtos = useProdutos()
  const ped = c.dossie?.pedido ?? {}
  const foto = fotoDoSku(produtos, ped.sku ?? c.decisao?.sku)
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-card ring-1 ring-borda"><CartaoEvidencia a={par.original} rotulo="Enviada antes" mesmoHash={par.mesmoHash} /></div>
        <div className="rounded-card ring-1 ring-borda"><CartaoEvidencia a={par.atual} rotulo="Recebida neste caso" mesmoHash={par.mesmoHash} carimbo={par.mesmoHash} /></div>
      </div>
      {foto && (
        <div className="flex items-center gap-3 rounded-card bg-sup2 px-3 py-2 text-[14px]">
          <Miniatura src={foto.img} alt={ped.descricao_produto ?? foto.nome} legenda="Foto de catálogo, não da cliente" className="h-14 w-14 rounded-[12px] bg-white object-contain" />
          <p className="min-w-0"><span className="text-suave">O que a cliente comprou: </span><b>{ped.descricao_produto ?? foto.nome}</b><span className="block text-[12.5px] text-suave">foto de catálogo, não da cliente</span></p>
        </div>
      )}
      {par.outros.length > 0 && (
        <div className="text-[14px]">
          <p className="font-semibold">A mesma foto já apareceu em {par.outros.length === 1 ? '1 outro pedido' : `${par.outros.length} outros pedidos`}</p>
          <ul className="mt-1 space-y-0.5 text-suave">
            {par.outros.map((o, i) => <li key={o.anexo_id ?? i}><Mono className="text-texto">{o.order_id ?? '—'}</Mono>, cliente <Mono className="text-texto">{mascararCliente(o.customer_id)}</Mono>{o.enviado_em ? `, em ${dataHora(o.enviado_em)}` : ''}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

export { Iniciais }

export function AvisoPortal({ c }: { c: Case }) {
  const transp = c.dossie?.rastreio?.transportadora ?? 'TransNorte'
  const href = `/portal/rastreio?pedido=${encodeURIComponent(c.order_id ?? '')}`
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card bg-espera-suave px-4 py-2.5 text-[14px]">
      <span className="font-semibold text-espera-texto">API indisponível → entrou no portal da {transp}</span>
      <a href={href} target="_blank" rel="noreferrer" className="ml-auto font-semibold text-estrutura hover:underline">Abrir o portal</a>
    </div>
  )
}

export function Evidencias({ itens }: { itens: unknown[] }) {
  return (
    <ul className="space-y-1.5">
      {itens.map((e, i) => {
        if (typeof e === 'string') return <li key={i} className="rounded-[12px] bg-sup2 px-3 py-2 text-[14px]">{e}</li>
        const o = e as Record<string, unknown>
        if (o.titulo) {
          const peso = typeof o.peso === 'number' ? o.peso : null
          return (
            <li key={i} className="rounded-[12px] bg-sup2 px-3 py-2 text-[14px]">
              <div className="flex items-start gap-2">
                <span className="flex-1 font-medium">{String(o.titulo)}</span>
                {peso != null && <span className={cx('num shrink-0 font-mono text-[12.5px] font-semibold', peso < 0 ? 'text-critico' : 'text-ok')}>{peso > 0 ? '+' : ''}{peso}</span>}
              </div>
              {o.detalhe ? <p className="mt-0.5 text-[13px] text-suave">{String(o.detalhe)}</p> : null}
            </li>
          )
        }
        return (
          <li key={i} className="flex flex-wrap gap-x-3 gap-y-0.5 rounded-[12px] bg-sup2 px-3 py-2 text-[13.5px]">
            {Object.entries(o).map(([k, v]) => (
              <span key={k}><span className="text-suave">{k.replace(/_/g, ' ')}</span>{' '}
                <span className={/id|hash|order|protocolo|codigo/.test(k) ? 'font-mono text-[12.5px]' : 'font-medium'}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
              </span>
            ))}
          </li>
        )
      })}
    </ul>
  )
}

