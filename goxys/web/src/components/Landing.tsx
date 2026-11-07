import { useEffect, useRef, useState } from 'react'
import type { Dict, Mascote } from '../types'
import { api } from '../api'
import { brl, cx, dataHora } from '../lib'
import { AvatarAgente, MascoteEstado, nomeAgente } from '../mascot'
import { mascararCliente, type AnexoEv } from './Comparacao'
import { GoxyFlip } from './GoxyFlip'
import { familiasPorPalavra, useProdutos } from '../produtos'
import { Miniatura } from '../lightbox'
import { useAuditoria } from '../auditoria'
import { Etiqueta, IconeCanal, Logo, NivelBadge, SetaCirculo } from './ui'
import { AnimatedBeam, AnimatedList, DotPattern, Marquee, NumberTicker, ShimmerButton } from './magic'

/** Dispara uma vez quando o bloco entra na tela. */
function useVisivel<T extends Element>(limiar = 0.35) {
  const ref = useRef<T>(null)
  const [vis, setVis] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (!('IntersectionObserver' in window)) { setVis(true); return }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setVis(true); io.disconnect() } }, { threshold: limiar })
    io.observe(el)
    return () => io.disconnect()
  }, [limiar])
  return [ref, vis] as const
}

const semMovimento = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

function Cta({ href, className, limao }: { href: string; className?: string; limao?: boolean }) {
  return (
    <a href={href} className={cx('inline-flex items-center gap-3 rounded-full py-2 pl-7 pr-2 text-[17px] font-bold transition hover:brightness-105', limao ? 'bg-limao text-azul' : 'bg-azul text-white', className)}>
      Ver o time trabalhando
      <SetaCirculo claro={!limao} size={40} />
    </a>
  )
}

export function Landing({ mascote, appHref }: { mascote: Mascote; appHref: string }) {
  const lm = useManifestoLanding()
  const ad = (lm?.adesivos ?? []).slice(0, 2)
  return (
    <div className="min-h-full">
      <BarraCompacta appHref={appHref} />

      {/* 1. herói: altura FIXA; o Goxy é o protagonista e os feixes saem e chegam nele */}
      <Heroi mascote={mascote} appHref={appHref} ad={ad} />

      <Gancho />
      <FaixaReclamacoes />

      <Historia mascote={mascote} />
      <Fraude mascote={mascote} />
      <Chamar m={lm} mascote={mascote} />
      {ad[3] && <div className="relative mx-auto h-0 max-w-[1180px]"><Adesivo src={ad[3]} pos="right-8 -top-12 w-20 rotate-[10deg]" /></div>}
      <Numeros />

      {/* 6. rodapé */}
      <footer className="mx-auto flex max-w-[1180px] flex-col items-center gap-5 px-5 py-16 text-center sm:px-8">
        <p className="max-w-[24ch] text-[clamp(24px,3vw,34px)] font-bold leading-tight tracking-[-0.015em]">O time já está trabalhando. Venha ver.</p>
        <Cta href={appHref} />
        <p className="flex flex-wrap items-center justify-center gap-2 text-[13.5px] text-suave">
          Feito para o GoHacks Talent 2026, o hackathon do
          <img src="/assets-goxys/marca/gogroup-logo.png" alt="Gogroup" className="h-7 w-auto min-w-[35px] p-[4px] dark:hidden" />
          <img src="/assets-goxys/marca/gogroup-logo-branco.png" alt="Gogroup" className="hidden h-7 w-auto min-w-[35px] p-[4px] dark:inline" />
        </p>
      </footer>
    </div>
  )
}

/* 2. Um contato, do começo ao fim */
const PASSOS: [string, string][] = [
  ['porteiro', 'entendeu: tampa rachada, quer troca'],
  ['rastreador', 'achou o pedido de R$ 89,90, entregue ontem'],
  ['detetive', 'conferiu a foto: inédita, tirada depois da entrega'],
  ['executor', 'gerou o reenvio de um copo novo'],
  ['voz', 'respondeu a cliente no WhatsApp'],
]

function Historia({ mascote }: { mascote: Mascote }) {
  const [ref, vis] = useVisivel<HTMLElement>(0.3)
  const [n, setN] = useState(0) // quantos passos já acenderam
  useEffect(() => {
    if (!vis) return
    if (semMovimento()) { setN(PASSOS.length + 1); return }
    let i = 0
    const t = setInterval(() => { i++; setN(i); if (i > PASSOS.length) clearInterval(t) }, 650)
    return () => clearInterval(t)
  }, [vis])
  const fim = n > PASSOS.length
  const progresso = (Math.min(n, PASSOS.length) / PASSOS.length) * 100

  return (
    <section ref={ref} aria-labelledby="historia" className="bg-sup">
      <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8">
        <Etiqueta>Ponta a ponta</Etiqueta>
        <h2 id="historia" className="mt-3 text-[clamp(26px,3vw,36px)] font-extrabold tracking-[-0.015em]">Um contato, do começo ao fim</h2>

        <div className="mt-8 max-w-[440px] rounded-[18px] rounded-tl-[4px] bg-[#ffffff] px-4 py-3 text-[16px] leading-relaxed text-[#333333] shadow-[0_1px_0_rgba(10,22,51,.08)]">
          Oi! Meu copo chegou com a tampa rachada, mandei a foto. Dá pra resolver?
          <span className="mt-1 block text-right text-[12px] text-[#666b75]">Marina, 07:51</span>
        </div>

        <ol className="relative mt-10 grid gap-6 md:grid-cols-5 md:gap-3">
          <span aria-hidden className="absolute left-[27px] top-0 h-full w-[3px] rounded-full bg-borda md:left-0 md:top-[27px] md:h-[3px] md:w-full" />
          <span aria-hidden className="absolute left-[27px] top-0 w-[3px] rounded-full bg-ok transition-[height] duration-500 ease-out md:hidden" style={{ height: `${progresso}%` }} />
          <span aria-hidden className="absolute left-0 top-[27px] hidden h-[3px] rounded-full bg-ok transition-[width] duration-500 ease-out md:block" style={{ width: `${progresso}%` }} />
          {PASSOS.map(([a, t], i) => {
            const aceso = n > i
            return (
              <li key={a} className="relative flex items-start gap-4 md:flex-col md:gap-3">
                <span className={cx('relative z-10 rounded-full bg-sup transition duration-500', aceso ? 'opacity-100' : 'opacity-35 grayscale')}>
                  <AvatarAgente agent={a} mascote={mascote} size={56} ativo={aceso && n === i + 1 && !fim} />
                </span>
                <p className={cx('pt-2 text-[15.5px] leading-snug transition-colors duration-500 md:pt-0', aceso ? 'text-texto' : 'text-suave')}>
                  <span className="font-bold">{nomeAgente(a)}</span> {t}
                </p>
              </li>
            )
          })}
        </ol>

        {fim && <div className="flutua-in mt-6 flex flex-col items-end gap-3">
          <div className="max-w-[480px] rounded-[18px] rounded-tr-[4px] bg-estrutura px-4 py-3 text-[16px] leading-relaxed text-white">
            Oi, Marina! Vi a foto e já resolvi: um copo novo sai pra você amanhã, sem custo. Não precisa devolver o outro.
          </div>
          <p className="text-[17px] font-bold text-ok">Resolvido em 30 s, sem ninguém tocar.</p>
        </div>}
      </div>
    </section>
  )
}

/* 3. O momento da fraude: o bloco de maior peso. Dados reais da base (conversa AT114498). */
const ANEXOS_REAIS: AnexoEv[] = [
  { anexo_id: 'IMG000123', order_id: 'GCH535078', customer_id: 'CH999406', canal: 'reclame_aqui', enviado_em: '2026-09-08 12:36:44', hash_perceptual: '41b2c50cabf85942', exif_data_captura: '2026-07-30 22:45:00', exif_modelo_camera: 'Galaxy S23', descricao_visual: 'Capinha de celular transparente com estampa rachada na lateral, perto dos botões.' },
  { anexo_id: 'IMG000267', order_id: 'GCH551285', customer_id: 'CH585887', canal: 'whatsapp', enviado_em: '2026-10-17 16:39:59', hash_perceptual: '41b2c50cabf85942', exif_data_captura: '2026-10-03 17:58:00', exif_modelo_camera: 'Galaxy S23', descricao_visual: 'Capinha de celular transparente com estampa rachada na lateral, perto dos botões.' },
]

function Fraude({ mascote }: { mascote: Mascote }) {
  const [anexos, setAnexos] = useState<AnexoEv[]>(ANEXOS_REAIS)
  const vichy = familiasPorPalavra(useProdutos(), ['vichy'])[0]
  const aud = useAuditoria()
  useEffect(() => {
    api.get<Dict[]>('/api/erp/anexos?hash=41b2c50cabf85942').then((l) => {
      const ids = ANEXOS_REAIS.map((x) => x.anexo_id)
      const reais = (Array.isArray(l) ? l : []).filter((x) => ids.includes(x.anexo_id)) as AnexoEv[]
      if (reais.length === 2) setAnexos(ids.map((id) => reais.find((r) => r.anexo_id === id)!))
    }).catch(() => {})
  }, [])
  const [orig, reuso] = anexos
  const SINAIS: [string, JSX.Element][] = [
    ['Mesma foto de outra cliente', <path key="a" d="M2 4h8v8H2zM6 1h8v8" />],
    ['Foto tirada antes da entrega (03/10 × 13/10)', <path key="b" d="M8 4v4l2.5 1.5M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Z" />],
    ['Estampa diferente da comprada', <path key="c" d="M3 3l10 10M13 3 3 13" />],
  ]
  return (
    <section aria-labelledby="fraude" className="scroll-mt-16 bg-azul text-white">
      <div className="mx-auto max-w-[1180px] px-5 py-12 sm:px-8">
        <div className="flex items-end gap-6">
          <div className="min-w-0 flex-1">
            <Etiqueta>Perícia</Etiqueta>
            <h2 id="fraude" className="mt-3 max-w-[26ch] text-[clamp(26px,3vw,38px)] font-extrabold leading-[1.1] tracking-[-0.02em] text-limao">
              A mesma foto já tinha sido enviada por outra cliente, 5 semanas antes.
            </h2>
          </div>
          <div className="hidden shrink-0 md:block"><MascoteEstado estado="fraude" mascote={mascote} size={150} fundo={false} /></div>
        </div>

        {/* 1. dois cartões ligados pelo selo da mesma imagem */}
        <div className="mt-6 grid items-stretch gap-3 md:grid-cols-[1fr_auto_1fr]">
          <CartaoFoto a={orig} data="08/09" />
          <div className="flex flex-row items-center justify-center gap-2 md:flex-col">
            <span className="hidden h-6 w-[3px] rounded-full bg-rosa md:block" />
            <span className="rounded-full bg-rosa px-3 py-1 text-center text-[13px] font-bold text-white">Mesma imagem</span>
            <span className="font-mono text-[12px] text-white">hash {orig.hash_perceptual ?? '41b2c50cabf85942'}</span>
            <span className="hidden h-6 w-[3px] rounded-full bg-rosa md:block" />
          </div>
          <CartaoFoto a={reuso} data="17/10" comprou={vichy ? { img: vichy.img, nome: vichy.nome } : undefined} />
        </div>

        {/* 2. três sinais */}
        <ul className="mt-5 grid gap-2 sm:grid-cols-3">
          {SINAIS.map(([t, ic]) => (
            <li key={t} className="flex items-center gap-3 rounded-[18px] bg-white px-4 py-3 text-[15px] font-semibold text-[#333333]">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-rosa text-white">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{ic}</svg>
              </span>{t}
            </li>
          ))}
        </ul>

        {/* 3. fecho */}
        <div className="mt-6 flex flex-wrap items-center gap-x-10 gap-y-3 border-t border-[#4f7dc2] pt-5">
          <p className="max-w-[46ch] flex-1 text-[clamp(17px,1.6vw,21px)] font-semibold leading-snug">
            Na vida real, a atendente pagou R$ 45,05 em 6 minutos. <span className="text-limao">O Goxy teria barrado.</span>
          </p>
          <p className="flex items-baseline gap-3">
            <span className="text-[clamp(34px,4vw,48px)] font-extrabold leading-none text-limao"><NumberTicker texto={String(aud.qtd)} /></span>
            <span className="text-[15px]">fotos assim,</span>
            <span className="text-[clamp(34px,4vw,48px)] font-extrabold leading-none text-limao"><NumberTicker texto={brl(aud.valor)} /></span>
          </p>
        </div>
      </div>
    </section>
  )
}

function CartaoFoto({ a, data, comprou }: { a: AnexoEv; data: string; comprou?: { img: string; nome: string } }) {
  return (
    <article className="flex min-w-0 flex-col rounded-[18px] bg-white p-4 text-[#333333]">
      <div className="flex items-baseline gap-3">
        <span className="text-[32px] font-extrabold leading-none text-azul">{data}</span>
        <span className="inline-flex items-center gap-1 text-[13.5px] text-[#5f6670]"><IconeCanal canal={a.canal ?? ''} size={14} />{NOME_CANAL_HEROI[a.canal ?? ''] ?? a.canal}</span>
        <span className="ml-auto font-mono text-[12.5px] text-[#5f6670]">{mascararCliente(a.customer_id)}</span>
      </div>
      <blockquote className="mt-2 line-clamp-3 text-[15px] leading-snug">"{a.descricao_visual}"</blockquote>
      <p className="mt-0.5 text-[12px] text-[#5f6670]">o que a plataforma de visão viu na foto</p>
      <p className="mt-2 text-[13px]"><span className="text-[#5f6670]">EXIF:</span> {a.exif_modelo_camera ?? 'sem câmera'}, {a.exif_data_captura ? dataHora(a.exif_data_captura).slice(0, 5) : 'sem data'}</p>
      {comprou && (
        <div className="mt-2 flex items-center gap-2 border-t border-[#eef1f6] pt-2 text-[13px]">
          <Miniatura src={comprou.img} alt={comprou.nome} legenda="Foto de catálogo, não da cliente" className="h-10 w-10 rounded-[10px] bg-white object-contain ring-1 ring-[#dde2ea]" />
          <span><span className="text-[#5f6670]">O que a cliente comprou: </span><b>{comprou.nome}</b></span>
        </div>
      )}
    </article>
  )
}

/* 4. Ele sabe quando chamar você */

const REGUA: { n: string; cor: string; fg: string; nome: string; cond: string; ex: string; estado: string }[] = [
  { n: 'A', cor: '#2659a5', fg: '#ffffff', nome: 'Resolve sozinho', cond: 'Até R$ 150 e prova clara', ex: 'Copo de R$ 111 com avaria comprovada: reenvio na hora', estado: 'resolvido' },
  { n: 'B', cor: '#2659a5', fg: '#ffffff', nome: 'Resolve e avisa', cond: 'Baixo risco e dá para desfazer em 15 min', ex: '', estado: 'resolvido' },
  { n: 'C', cor: '#f8ae13', fg: '#333333', nome: 'Pede sua aprovação', cond: 'Valor alto ou prova duvidosa', ex: '', estado: 'esperando' },
  { n: 'D', cor: '#e5273c', fg: '#ffffff', nome: 'Passa para uma pessoa', cond: 'Processo, Procon, fraude ou caso público', ex: 'Cliente que ameaça o Procon e o Reclame Aqui', estado: 'humano' },
]

function Chamar({ m, mascote }: { m: ManifestoLanding | null; mascote: Mascote }) {
  return (
    <section aria-labelledby="chamar" className="mx-auto max-w-[1180px] scroll-mt-16 px-5 py-10 sm:px-8">
      <Etiqueta>Autonomia</Etiqueta>
      <h2 id="chamar" className="mt-3 text-[clamp(26px,3vw,36px)] font-extrabold tracking-[-0.015em]">Ele sabe quando chamar você</h2>
      <div className="mt-5 grid items-start gap-6 lg:grid-cols-[1fr_250px]">
        <div>
          {/* régua de risco */}
          <div className="mb-3 flex items-center gap-2 text-[12.5px] font-semibold text-suave">
            <span>menos risco</span>
            <span className="grid h-1.5 flex-1 grid-cols-4 gap-1">{REGUA.map((r) => <span key={r.n} className="rounded-full" style={{ background: r.cor }} />)}</span>
            <span>mais risco</span>
          </div>
          <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {REGUA.map((r) => (
              <li key={r.n} className={cx('relative flex flex-col rounded-[18px] bg-sup p-4', r.n === 'C' && 'ring-2 ring-laranja')}>
                <div className="flex items-start justify-between">
                  <span className="flex h-12 w-12 items-center justify-center rounded-[14px] text-[24px] font-extrabold" style={{ background: r.cor, color: r.fg }}>{r.n}</span>
                  <MascoteEstado estado={r.estado} mascote={mascote} size={58} fundo={false} />
                </div>
                <p className="mt-3 text-[16px] font-semibold leading-tight">{r.nome}</p>
                <p className="mt-1 text-[13.5px] text-suave">{r.cond}</p>
                {r.ex && <p className="mt-3 border-t border-borda pt-2 text-[13.5px] leading-snug">{r.ex}</p>}
                {r.n === 'C' && <span className="absolute -right-3 top-1/2 hidden h-[3px] w-6 rounded-full bg-laranja lg:block" aria-hidden />}
              </li>
            ))}
          </ol>
          <p className="mt-5 text-[15px] font-semibold">O Goxy nunca tem mais alçada que um atendente: reembolsa até R$ 100 e reenvia até R$ 200.</p>
        </div>
        <div className="hidden lg:block">
          <div className="h-[410px] origin-top scale-[0.72]"><Celular m={m} /></div>
        </div>
      </div>
    </section>
  )
}

/* 5. Números reais */
function Numeros() {
  const [nums, setNums] = useState<{ v: string; r: string }[] | null>(null)
  useEffect(() => {
    Promise.allSettled([api.get<Dict>('/api/goxys/analista'), api.get<Dict>('/api/goxys/metricas'), api.get<Dict>('/api/goxys/placar')]).then(([a, m, p]) => {
      const an = a.status === 'fulfilled' ? a.value : {}
      const me = m.status === 'fulfilled' ? m.value : {}
      const pl = p.status === 'fulfilled' ? p.value : {}
      const out: { v: string; r: string }[] = []
      const evit = an?.resumo?.contatos_evitaveis_total ?? me?.contatos_evitados
      if (evit != null) out.push({ v: Number(evit).toLocaleString('pt-BR'), r: 'contatos que dá para evitar avisando antes' })
      if (an?.resumo?.economia_estimada_rs != null) out.push({ v: brl(an.resumo.economia_estimada_rs), r: 'de economia estimada com esses avisos' })
      if (me?.tempo_medio_s && me?.baseline?.tempo_medio_humano_min) out.push({ v: `${Math.round(me.tempo_medio_s)} s`, r: `por caso, contra ${String(me.baseline.tempo_medio_humano_min).replace('.', ',')} min de uma pessoa` })
      else if (an?.resumo?.conversas_repetidas != null) out.push({ v: Number(an.resumo.conversas_repetidas).toLocaleString('pt-BR'), r: 'conversas repetidas no histórico' })
      if (pl?.total) out.push({ v: `${pl.acertos}/${pl.total}`, r: 'clientes sintéticos atendidos do jeito certo' })
      setNums(out.slice(0, 4))
    })
  }, [])
  return (
    <section aria-label="Números" className="border-y border-borda bg-sup">
      <div className="mx-auto grid max-w-[1180px] gap-x-12 gap-y-8 px-5 py-14 sm:grid-cols-2 sm:px-8 lg:grid-cols-4">
        {(nums ?? []).map((n, i) => (
          <div key={i}>
            <p className="num whitespace-nowrap text-[clamp(30px,3vw,40px)] font-extrabold leading-none tracking-[-0.02em] text-estrutura"><NumberTicker texto={n.v} /></p>
            <p className="mt-2 max-w-[26ch] text-[15px] text-suave">{n.r}</p>
          </div>
        ))}
        {nums && nums.length === 0 && <p className="text-suave">Os números aparecem quando o backend está no ar.</p>}
      </div>
    </section>
  )
}

/* Decoração gerada no ComfyUI (assets/landing). Tudo opcional: sem manifesto, a landing fica como está. */
interface ManifestoLanding { celular?: { img: string; tela: { x: number; y: number; w: number; h: number } }; adesivos: string[]; fundo?: string }

function useManifestoLanding(): ManifestoLanding | null {
  const [m, setM] = useState<ManifestoLanding | null>(null)
  useEffect(() => {
    fetch('/assets-goxys/landing/manifest.json').then((r) => (r.ok ? r.json() : null)).then((j: Dict | null) => {
      if (!j) return
      const base = '/assets-goxys/landing/'
      const url = (x: unknown): string | undefined => {
        const s = typeof x === 'string' ? x : (x as Dict)?.img ?? (x as Dict)?.src
        return s ? (s.startsWith('/') || s.startsWith('http') ? s : base + s) : undefined
      }
      const cel = j.celular, img = url(cel)
      const t = cel?.tela
      setM({
        celular: img && t && [t.x, t.y, t.w, t.h].every((v: unknown) => typeof v === 'number') ? { img, tela: t } : undefined,
        adesivos: (Array.isArray(j.adesivos) ? j.adesivos : []).map(url).filter(Boolean) as string[],
        fundo: url(j.fundo),
      })
    }).catch(() => {})
  }, [])
  return m
}

const POS_ADESIVO = ['left-[52%] top-2 w-16 -rotate-12', 'right-6 bottom-6 w-20 rotate-6', 'left-2 -bottom-6 w-14 rotate-[8deg]']

function Adesivo({ src, pos }: { src: string; pos?: string }) {
  return <img src={src} alt="" aria-hidden className={cx('adesivo pointer-events-none absolute z-0 hidden select-none opacity-90 md:block', pos)} onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
}

function TelaNotificacao({ altura }: { altura?: string }) {
  return (
    <div className={cx('relative overflow-hidden bg-gradient-to-b from-[#2659a5] to-[#16335c] px-4 pt-10', altura ?? 'h-full')}>
      <p className="num text-center text-[52px] font-light leading-none text-white">08:03</p>
      <p className="mt-1 text-center text-[14px] text-white/80">segunda, 2 de novembro</p>
      <div className="mt-8 rounded-[22px] bg-white p-4 text-[#333333] shadow-lg">
        <div className="flex items-center gap-2 text-[12.5px] text-[#666b75]">
          <span className="flex h-5 w-5 items-center justify-center rounded-[6px] bg-[#2659a5] text-[11px] font-bold text-white">G</span>
          Goxy, agora
        </div>
        <p className="mt-2 text-[15px] font-bold leading-snug">Reembolso de R$ 689 com foto duvidosa</p>
        <p className="mt-1 text-[13.5px] leading-snug text-[#666b75]">Foto sem EXIF, imagem ambígua. Veracidade 55. Precisa da sua decisão.</p>
        <div className="mt-3 grid grid-cols-3 gap-1.5 text-[13px] font-semibold">
          <span className="rounded-full bg-[#2659a5] py-2 text-center text-white">Aprovar</span>
          <span className="rounded-full bg-[#eef1f6] py-2 text-center text-[#e5273c]">Negar</span>
          <span className="rounded-full bg-[#eef1f6] py-2 text-center">Ajustar</span>
        </div>
      </div>
      <p className="mt-4 text-center text-[12.5px] text-white/70">O mesmo pedido chega no Telegram, no Slack e no painel.</p>
    </div>
  )
}

function Celular({ m }: { m: ManifestoLanding | null }) {
  const [falhou, setFalhou] = useState(false)
  const c = m?.celular
  if (c && !falhou) {
    const t = c.tela
    return (
      <div className="relative mx-auto w-[320px]">
        <img src={c.img} alt="" aria-hidden className="relative z-10 block w-full select-none" onError={() => setFalhou(true)} />
        <div className="absolute overflow-hidden rounded-[30px]" style={{ left: `${t.x}%`, top: `${t.y}%`, width: `${t.w}%`, height: `${t.h}%` }}>
          <TelaNotificacao />
        </div>
      </div>
    )
  }
  return (
    <div className="mx-auto w-[300px] rounded-[44px] bg-[#2659a5] p-3 shadow-[0_30px_70px_-30px_rgba(7,15,38,.7)]">
      <div className="relative overflow-hidden rounded-[34px]">
        <span aria-hidden className="absolute left-1/2 top-3 z-10 h-5 w-24 -translate-x-1/2 rounded-full bg-[#2659a5]" />
        <TelaNotificacao altura="h-[540px]" />
      </div>
    </div>
  )
}

/* Gancho logo abaixo da dobra: o número real mais forte. */
function Gancho() {
  const a = useAuditoria()
  return (
    <section aria-label="Auditoria da base real" className="mx-auto max-w-[1180px] px-4 sm:px-8">
      <div className="flex flex-wrap items-center gap-x-10 gap-y-3 rounded-[40px] bg-limao px-8 py-8 text-azul sm:px-10">
        <p className="text-[clamp(44px,6vw,72px)] font-extrabold leading-none tracking-[-0.02em]"><NumberTicker texto={brl(a.valor)} /></p>
        <p className="max-w-[36ch] text-[clamp(18px,2vw,22px)] font-semibold leading-snug">
          <NumberTicker texto={String(a.qtd)} /> fotos reaproveitadas de outros clientes foram pagas na base real. <span className="font-extrabold">O Goxy teria barrado as {a.qtd}.</span>
        </p>
      </div>
    </section>
  )
}


/* Feixes do herói: canais -> Goxy -> sistemas */
const CANAIS = ['whatsapp', 'email', 'reclame_aqui', 'slack'] as const
const SISTEMAS: [string, JSX.Element][] = [
  ['ERP', <path key="e" d="M3 4h10v8H3zM3 7h10M6 4v8" />],
  ['Financeiro', <path key="f" d="M8 2v12M11 5H6.5a1.8 1.8 0 0 0 0 3.6h3a1.8 1.8 0 0 1 0 3.6H5" />],
  ['Logística', <path key="l" d="M1.5 4h8v6h-8zM9.5 6.5h3l2 2V10h-5M4 12.5a1 1 0 1 0 0-.1M12 12.5a1 1 0 1 0 0-.1" />],
  ['Transportadora', <path key="t" d="M8 14s5-4.5 5-8A5 5 0 0 0 3 6c0 3.5 5 8 5 8ZM8 7.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" />],
]
const NOME_CANAL_HEROI: Record<string, string> = { whatsapp: 'WhatsApp', email: 'E-mail', reclame_aqui: 'Reclame Aqui', telegram: 'Telegram', slack: 'Slack' }

/** Barra compacta e sólida que só aparece depois da primeira dobra. */
function BarraCompacta({ appHref }: { appHref: string }) {
  const [ver, setVer] = useState(false)
  useEffect(() => {
    const f = () => setVer(window.scrollY > Math.min(900, Math.max(560, window.innerHeight)) - 80)
    f(); window.addEventListener('scroll', f, { passive: true })
    return () => window.removeEventListener('scroll', f)
  }, [])
  if (!ver) return null
  return (
    <header className="flutua-in fixed inset-x-0 top-0 z-40 border-b border-borda bg-sup">
      <div className="mx-auto flex max-w-[1180px] items-center px-5 py-2.5 sm:px-8">
        <a href="/" aria-label="Goxy, início"><Logo size={28} /></a>
        <a href={appHref} className="ml-auto inline-flex items-center gap-2 rounded-full bg-azul py-1 pl-4 pr-1 text-[14.5px] font-semibold text-white">Abrir o painel<SetaCirculo claro size={28} /></a>
      </div>
    </header>
  )
}

function Heroi({ mascote, appHref, ad }: { mascote: Mascote; appHref: string; ad: string[] }) {
  const [acena, setAcena] = useState(0)
  const caixa = useRef<HTMLDivElement>(null)
  const centro = useRef<HTMLDivElement>(null)
  const canais = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)]
  const sist = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)]
  const no = 'flex h-11 w-11 items-center justify-center rounded-full bg-white text-azul shadow-[0_6px_18px_-8px_rgba(0,0,0,.45)]'
  const ARCO = [[12, 16], [5, 39], [5, 62], [12, 85]]
  return (
    <section className="relative p-3 sm:p-4" style={{ height: 'clamp(560px, 100svh, 900px)' }}>
      {ad.map((src, i) => <Adesivo key={src} src={src} pos={POS_ADESIVO[i]} />)}
      <div className="relative h-full overflow-hidden rounded-[40px] border-[6px] border-limao bg-azul text-white">
        <DotPattern className="text-[#4f7dc2]" />

        {/* navbar dentro do painel */}
        <nav className="absolute inset-x-0 top-0 z-30 flex items-center gap-4 px-5 py-4 sm:px-8" aria-label="Topo">
          <a href="/" aria-label="Goxy, início"><Logo claro /></a>
          <img src="/assets-goxys/marca/gogroup-logo-branco.png" alt="Gogroup" className="ml-auto hidden h-6 w-auto min-w-[35px] p-[4px] sm:block" />
          <a href={appHref} className="ml-auto inline-flex items-center gap-2 rounded-full bg-limao py-1 pl-4 pr-1 text-[14.5px] font-bold text-azul sm:ml-0">Abrir o painel<SetaCirculo size={30} /></a>
        </nav>

        <div className="absolute inset-x-0 bottom-12 top-20 grid grid-rows-[52%_1fr] gap-2 px-5 sm:px-8 md:grid-cols-[38%_1fr] md:grid-rows-1 md:gap-6">
          {/* texto + CTA + balão (no celular, abaixo do Goxy) */}
          <div className="relative z-20 order-2 flex flex-col justify-center md:order-1">
            <Etiqueta className="self-start">Atendimento Gogroup</Etiqueta>
            <h1 className="mt-3 max-w-[15ch] text-[clamp(28px,3.3vw,46px)] font-extrabold leading-[1.05] tracking-[-0.03em] text-limao">
              O atendimento que investiga antes de pagar.
            </h1>
            <p className="mt-3 max-w-[40ch] text-[clamp(14.5px,1.2vw,16.5px)] leading-snug text-white">
              Uma equipe de agentes que investiga, decide e resolve o atendimento. E chama você quando precisa.
            </p>
            <span onMouseEnter={() => setAcena((x) => x + 1)} onFocus={() => setAcena((x) => x + 1)} className="mt-5 inline-block self-start">
              <ShimmerButton href={appHref} className="ring-2 ring-white">Ver o time trabalhando<SetaCirculo claro size={40} /></ShimmerButton>
            </span>
            <div className="mt-5 hidden w-[300px] [@media(min-height:640px)]:md:block"><ContatosChegando /></div>
          </div>

          {/* diagrama: canais -> Goxy -> sistemas; nada por cima do Goxy */}
          <div ref={caixa} className="relative order-1 min-h-0 md:order-2">
            <div className="hidden md:block">
              {CANAIS.map((k, i) => (
                <div key={k} className="absolute z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1" style={{ left: `${ARCO[i][0]}%`, top: `${ARCO[i][1]}%` }}>
                  <div ref={canais[i]} className={no}><IconeCanal canal={k} size={20} /></div>
                  <span className="whitespace-nowrap text-[11px] font-semibold text-white">{NOME_CANAL_HEROI[k]}</span>
                </div>
              ))}
              {SISTEMAS.map(([nome, icone], i) => (
                <div key={nome} className="absolute z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1" style={{ left: `${100 - ARCO[i][0]}%`, top: `${ARCO[i][1]}%` }}>
                  <div ref={sist[i]} className={no}><svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{icone}</svg></div>
                  <span className="whitespace-nowrap text-[11px] font-semibold text-white">{nome}</span>
                </div>
              ))}
              {canais.map((r, i) => <AnimatedBeam key={`c${i}`} container={caixa} de={r} para={centro} curva={(i - 1.5) * 14} atraso={i * 0.35} />)}
              {sist.map((r, i) => <AnimatedBeam key={`s${i}`} container={caixa} de={centro} para={r} curva={(i - 1.5) * -14} atraso={1.2 + i * 0.35} />)}
            </div>
            <div ref={centro} className="absolute bottom-0 left-1/2 top-0 z-20 aspect-square max-w-full -translate-x-1/2 md:max-w-[62%]">
              <div key={acena} className={cx('relative h-full w-full', acena > 0 && 'goxy-quique')}>
                <GoxyFlip mascote={mascote} />
              </div>
            </div>
          </div>
        </div>

        {/* indicador de rolagem */}
        <a href="#historia" className="absolute bottom-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1.5 text-[13px] font-semibold text-white">
          Role para ver o caso real
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden className="ao-vivo"><path d="m4 6 4 4 4-4" /></svg>
        </a>
      </div>
    </section>
  )
}

/* Contatos chegando um a um, com o selo do resultado */
type Chegada = { id: string; titulo: string; nome: string; nivel?: string; selo: string; cor: string }
const SELO: Record<string, [string, string]> = {
  resolvido: ['Resolvido', 'bg-azul text-white'], rejeitado: ['Barrado', 'bg-carmim text-white'], aguardando_aprovacao: ['Precisa de você', 'bg-laranja text-[#333333]'],
  humano: ['Com uma pessoa', 'bg-ciano text-[#333333]'], em_andamento: ['Trabalhando', 'bg-[#eef1f6] text-[#333333]'], novo: ['Novo', 'bg-[#eef1f6] text-[#333333]'],
}
const CHEGADAS_RESERVA: Chegada[] = [
  { id: 'r1', titulo: 'Copo com tampa rachada', nome: 'Manuela R.', nivel: 'A', selo: 'Resolvido', cor: SELO.resolvido[1] },
  { id: 'r2', titulo: 'Capinha com foto reaproveitada', nome: 'Joana A.', nivel: 'D', selo: 'Barrado', cor: SELO.rejeitado[1] },
  { id: 'r3', titulo: 'Garrafa parada na transportadora', nome: 'Tainá R.', nivel: 'A', selo: 'Resolvido', cor: SELO.resolvido[1] },
  { id: 'r4', titulo: 'Reembolso de R$ 689,70', nome: 'Camila C.', nivel: 'C', selo: 'Precisa de você', cor: SELO.aguardando_aprovacao[1] },
  { id: 'r5', titulo: 'Ameaça de ir ao Procon', nome: 'Rodrigo S.', nivel: 'D', selo: 'Com uma pessoa', cor: SELO.humano[1] },
]
const mascararNome = (n?: string | null) => { const p = (n ?? 'Cliente').split(' '); return p.length > 1 ? `${p[0]} ${p[p.length - 1][0]}.` : p[0] }

function ContatosChegando() {
  const [itens, setItens] = useState<Chegada[]>(CHEGADAS_RESERVA)
  useEffect(() => {
    api.get<Dict[]>('/api/goxys/casos').then((l) => {
      const reais = (l ?? []).filter((c) => c.status && c.status !== 'novo').slice(0, 8).map((c): Chegada => {
        const [selo, cor] = SELO[c.status] ?? SELO.em_andamento
        return { id: c.id, titulo: c.titulo ?? 'Contato', nome: mascararNome(c.cliente_nome), nivel: c.nivel, selo, cor }
      })
      if (reais.length >= 3) setItens(reais)
    }).catch(() => {})
  }, [])
  return (
    <div className="rounded-card bg-white p-2.5 text-[#333333] shadow-[0_14px_30px_-14px_rgba(0,0,0,.45)]">
      <p className="px-1 pb-1.5 text-[12px] font-semibold text-[#5f6670]">Contatos chegando agora</p>
      <div className="h-[104px] overflow-hidden" style={{ maskImage: 'linear-gradient(to bottom, black 70%, transparent)', WebkitMaskImage: 'linear-gradient(to bottom, black 70%, transparent)' }}>
      <AnimatedList itens={itens} visiveis={3} render={(it: Chegada) => (
        <div className="flex h-[44px] items-center gap-2 rounded-[12px] bg-[#f5f5f5] px-2">
          <NivelBadge nivel={it.nivel} />
          <div className="min-w-0 flex-1"><p className="truncate text-[12.5px] font-semibold leading-tight">{it.titulo}</p><p className="text-[11px] text-[#5f6670]">{it.nome}</p></div>
          <span className={cx('shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold', it.cor)}>{it.selo}</span>
        </div>
      )} />
      </div>
    </div>
  )
}

/* Marquee com títulos reais do Reclame Aqui simulado (sem nomes) */
function FaixaReclamacoes() {
  const [titulos, setTitulos] = useState<string[]>([])
  useEffect(() => {
    api.get<Dict[]>('/api/reclameaqui/reclamacoes?limite=20').then((l) => {
      const t = (l ?? []).map((r) => String(r.titulo ?? '').replace(/\b[A-ZÁÉÍÓÚ][a-záéíóúãõç]+ [A-ZÁÉÍÓÚ][a-záéíóúãõç]+\b/g, 'cliente').trim()).filter(Boolean)
      setTitulos([...new Set(t)].slice(0, 16))
    }).catch(() => {})
  }, [])
  return (
    <section aria-label="Reclamações reais que chegam" className="h-[118px] py-8">
      <p className="mx-auto max-w-[1180px] px-5 pb-3 text-[14px] font-semibold text-suave sm:px-8">O que chega no Reclame Aqui</p>
      {titulos.length >= 4 && <Marquee duracao={70}>
        {titulos.map((t) => <span key={t} className="whitespace-nowrap rounded-full bg-sup px-4 py-2 text-[14.5px] ring-1 ring-borda">{t}</span>)}
      </Marquee>}
    </section>
  )
}
