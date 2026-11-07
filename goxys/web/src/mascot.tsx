import { useEffect, useState } from 'react'
import type { Dict, Mascote, MidiaMascote } from './types'

export const BASE_GOXY = '/assets-goxys/goxy.png'
/** Versão recortada (transparente e normalizada) do personagem base. */
export const BASE_TRANSP = '/assets-goxys/goxys/base.webp'
/** Mesmo enquadramento para qualquer imagem transparente; só o PNG creme antigo vai recortado em círculo. */
const classeImg = (src: string) => (src === BASE_GOXY ? 'relative h-[80%] w-[80%] rounded-full object-cover' : 'relative h-full w-full object-contain')

export const COR_AGENTE: Record<string, string> = {
  maestro: 'var(--ag-maestro)', porteiro: 'var(--ag-porteiro)', rastreador: 'var(--ag-rastreador)',
  detetive: 'var(--ag-detetive)', executor: 'var(--ag-executor)', voz: 'var(--ag-voz)',
  mensageiro: 'var(--ag-mensageiro)', analista: 'var(--ag-analista)', politica: 'var(--ag-politica)',
}
export const corAgente = (a?: string | null) => COR_AGENTE[a ?? ''] ?? 'var(--estrutura)'

export const NOME_AGENTE: Record<string, string> = {
  maestro: 'Maestro', porteiro: 'Porteiro', rastreador: 'Rastreador', detetive: 'Detetive',
  executor: 'Executor', voz: 'Voz', mensageiro: 'Mensageiro', analista: 'Analista', politica: 'Política',
}
export const nomeAgente = (a?: string | null) => NOME_AGENTE[a ?? ''] ?? (a ?? '')

/** Aceita {agente: "url"} ou {agente: {img, video, poster}}. */
export function normalizarMascote(m: Dict | null | undefined): Mascote {
  const norm = (o: Dict | undefined) => {
    const r: Record<string, MidiaMascote> = {}
    for (const [k, v] of Object.entries(o ?? {})) {
      if (!v) continue
      if (typeof v === 'string') r[k.toLowerCase()] = /\.(webm|mp4)$/i.test(v) ? { video: v } : { img: v }
      else r[k.toLowerCase()] = { video: v.video ?? undefined, img: v.img ?? v.imagem ?? undefined, img_sm: v.img_sm ?? undefined, poster: v.poster ?? v.img ?? undefined }
    }
    return r
  }
  const h = m?.hero
  const hero = typeof h === 'string' ? { video: h } : h ? { video: h.video ?? undefined, poster: h.poster ?? h.img ?? undefined } : undefined
  return { agentes: norm(m?.agentes), estados: norm(m?.estados), base: typeof m?.base === 'string' ? m.base : (m?.base?.img ?? m?.base?.png ?? undefined), base_sm: typeof m?.base === 'object' ? m?.base?.img_sm ?? undefined : undefined, hero }
}

let _alfa: boolean | null = null
/** VP9 com alfa: Chrome/Firefox sim; Safari não (usa o pôster). */
export function suportaWebmAlfa(): boolean {
  if (_alfa !== null) return _alfa
  const ua = navigator.userAgent
  const safari = /safari/i.test(ua) && !/chrome|chromium|crios|android|edg/i.test(ua)
  const v = document.createElement('video')
  _alfa = !safari && v.canPlayType('video/webm; codecs="vp9"') !== ''
  return _alfa
}

/** Imagem com cadeia de reservas: tenta cada src; se todas falharem, mostra o SVG. */
function ImgComReserva({ srcs, alt, svg }: { srcs: string[]; alt: string; svg: JSX.Element }) {
  const [i, setI] = useState(0)
  const lista = srcs.filter(Boolean)
  useEffect(() => setI(0), [lista.join('|')])
  if (i >= lista.length) return svg
  return <img src={lista[i]} alt={alt} className={classeImg(lista[i])} onError={() => setI((x) => x + 1)} draggable={false} />
}

/** Avatar pequeno do agente (linha do tempo, cartões). */
export function AvatarAgente({ agent, mascote, size = 40, ativo = false }: { agent: string; mascote: Mascote; size?: number; ativo?: boolean }) {
  const cor = corAgente(agent)
  const m = mascote.agentes[agent]
  const proprio = m?.img_sm || m?.img || m?.poster
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full ${ativo ? 'pulsa' : ''}`}
      style={{ width: size, height: size, background: `color-mix(in srgb, ${cor} 14%, var(--superficie))`, boxShadow: `inset 0 0 0 2px ${cor}`, ['--cor' as string]: cor }}
      title={nomeAgente(agent)}
    >
      <ImgComReserva
        srcs={[proprio ?? '', BASE_TRANSP, BASE_GOXY]}
        alt={nomeAgente(agent)}
        svg={<GoxySvg agent={agent} size={size} />}
      />
      {!proprio && size >= 32 && (
        <span className="absolute -bottom-1 -right-1 flex items-center justify-center rounded-full border-2 border-[var(--superficie)]" style={{ width: size * 0.42, height: size * 0.42, background: cor }}>
          <Acessorio agent={agent} size={size * 0.28} />
        </span>
      )}
    </span>
  )
}

/** Mascote grande do estado do caso, flutuando sobre o fundo. Anima por CSS enquanto os vídeos não chegam. */
export function MascoteEstado({ estado, agent, mascote, size = 148, fundo = true }: { estado: string; agent?: string | null; mascote: Mascote; size?: number; fundo?: boolean }) {
  const e = mascote.estados[estado]
  const a = agent ? mascote.agentes[agent] : undefined
  const [videoFalhou, setVideoFalhou] = useState(false)
  useEffect(() => setVideoFalhou(false), [e?.video])
  const usarVideo = e?.video && !videoFalhou && (suportaWebmAlfa() || !/\.webm$/i.test(e.video))
  if (usarVideo) {
    return (
      <video key={e!.video} src={e!.video} poster={e!.poster} autoPlay loop muted playsInline onError={() => setVideoFalhou(true)}
        style={{ width: size, height: size }} className="pointer-events-none object-contain" aria-label={`Goxy ${estado}`} />
    )
  }
  const proprio = e?.poster || e?.img
  const srcs = [proprio ?? '', a?.img ?? '', BASE_TRANSP, BASE_GOXY]
  return (
    <span className={`goxy-anim goxy-${estado} relative inline-flex items-center justify-center`} style={{ width: size, height: size }}>
      {fundo && (
        <span className="absolute inset-[6%] rounded-full transition-colors duration-500" style={{ background: `color-mix(in srgb, ${corEstado(estado)} 22%, var(--superficie))` }} />
      )}
      <ImgComReserva
        srcs={srcs}
        alt={`Goxy ${estado}`}
        svg={<span className="relative"><GoxySvg agent={agent ?? 'maestro'} size={size * 0.78} estado={estado} /></span>}
      />
    </span>
  )
}

export function corEstado(estado: string) {
  return ({ parado: 'var(--estrutura)', investigando: 'var(--marca)', fraude: 'var(--critico)', esperando: 'var(--espera)', resolvido: 'var(--ok)', humano: 'var(--estrutura)' } as Record<string, string>)[estado] ?? 'var(--borda)'
}

/** Ícone curto do papel de cada agente. */
function Acessorio({ agent, size }: { agent: string; size: number }) {
  const p = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: agent === 'voz' || agent === 'rastreador' ? '#333333' : '#fff', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  switch (agent) {
    case 'maestro': return <svg {...p}><path d="M3 13 13 3M10 3h3v3" /></svg>
    case 'porteiro': return <svg {...p}><path d="M3 4h10M3 8h7M3 12h4" /></svg>
    case 'rastreador': return <svg {...p}><path d="M8 14s5-4.5 5-8A5 5 0 0 0 3 6c0 3.5 5 8 5 8Z" /><circle cx="8" cy="6" r="1.5" /></svg>
    case 'detetive': return <svg {...p}><circle cx="7" cy="7" r="4" /><path d="m10 10 4 4" /></svg>
    case 'executor': return <svg {...p}><path d="m3 8.5 3 3 7-7" /></svg>
    case 'voz': return <svg {...p}><path d="M3 4h10v6H7l-3 3v-3H3z" /></svg>
    case 'mensageiro': return <svg {...p}><path d="M8 2v2M4 7a4 4 0 0 1 8 0v3l1.5 2h-11L4 10z" /></svg>
    case 'analista': return <svg {...p}><path d="M3 13V9M8 13V4M13 13V7" /></svg>
    default: return <svg {...p}><circle cx="8" cy="8" r="3" /></svg>
  }
}

/** Último recurso: Goxy desenhado (nuvem creme, contorno escuro, bochechas na cor do agente). */
export function GoxySvg({ agent, size = 40, estado }: { agent: string; size?: number; estado?: string }) {
  const cor = corAgente(agent)
  const boca = estado === 'fraude' ? 'M27 41q5-3 10 0' : estado === 'esperando' ? 'M28 40h8' : 'M27 39q5 5 10 0'
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <path d="M18 50c-8 0-12-6-10-12-5-3-4-12 3-13 0-9 9-13 16-9 4-6 15-6 18 2 8-1 13 7 9 13 5 4 2 13-5 13-1 5-6 8-11 6-4 3-12 3-15 0z" fill="#fdf1d8" stroke="#13161c" strokeWidth="2.6" strokeLinejoin="round" />
      <ellipse cx="25" cy="31" rx="3.4" ry="4.4" fill="#13161c" />
      <ellipse cx="39" cy="31" rx="3.4" ry="4.4" fill="#13161c" />
      <circle cx="26" cy="29.5" r="1.1" fill="#fff" />
      <circle cx="40" cy="29.5" r="1.1" fill="#fff" />
      <ellipse cx="19" cy="38" rx="3.4" ry="2.2" fill={cor} />
      <ellipse cx="45" cy="38" rx="3.4" ry="2.2" fill={cor} />
      <path d={boca} stroke="#13161c" strokeWidth="2.4" fill="none" strokeLinecap="round" />
    </svg>
  )
}

/** Goxy do herói da landing: vídeo com alfa quando existir; senão pôster/base com a respiração CSS. */
export function HeroGoxy({ mascote, size = 380 }: { mascote: Mascote; size?: number }) {
  const h = mascote.hero
  const [falhou, setFalhou] = useState(false)
  if (h?.video && !falhou && (suportaWebmAlfa() || !/\.webm$/i.test(h.video))) {
    return <video src={h.video} poster={h.poster} autoPlay loop muted playsInline onError={() => setFalhou(true)} style={{ width: size, height: size }} className="pointer-events-none object-contain" aria-label="Goxy" />
  }
  return (
    <span className="goxy-anim goxy-parado relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <ImgComReserva srcs={[h?.poster ?? '', BASE_TRANSP, BASE_GOXY]} alt="Goxy, o mascote do time" svg={<GoxySvg agent="maestro" size={size * 0.8} />} />
    </span>
  )
}

/** O Goxy comum (personagem base transparente) com a respiração sutil: usado em qualquer carregamento. */
export function GoxyComum({ size = 180 }: { size?: number }) {
  return (
    <span className="goxy-anim goxy-parado relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <ImgComReserva srcs={['/assets-goxys/goxys/base.webp', BASE_GOXY]} alt="Goxy" svg={<GoxySvg agent="maestro" size={size * 0.8} />} />
    </span>
  )
}

/** Imagem pequena (miniaturas) com reserva na grande. */
export const imgPequena = (m?: MidiaMascote) => m?.img_sm ?? m?.img ?? m?.poster
/** srcset 512w/1024w quando o manifest traz as duas versões. */
export const srcSetDe = (sm?: string, lg?: string) => (sm && lg && sm !== lg ? `${sm} 512w, ${lg} 1024w` : undefined)
