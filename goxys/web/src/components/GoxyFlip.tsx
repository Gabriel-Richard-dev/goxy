import { useEffect, useRef, useState } from 'react'
import type { Mascote } from '../types'
import { BASE_GOXY, nomeAgente, srcSetDe, suportaWebmAlfa } from '../mascot'

// O Goxy do herói "flipa" entre os agentes: base -> Porteiro -> ... -> Maestro -> base.
// Cada personagem segue animado com o próprio idle (webm com alfa) ou com a respiração CSS de reserva.
const ORDEM = ['base', 'porteiro', 'rastreador', 'detetive', 'executor', 'voz', 'mensageiro', 'analista', 'maestro']
const PAPEL: Record<string, string> = {
  base: 'o time inteiro', porteiro: 'entende o que o cliente quer', rastreador: 'junta pedido e entrega',
  detetive: 'confere se a prova é real', executor: 'troca, reenvia ou reembolsa', voz: 'responde no tom da casa',
  mensageiro: 'chama você quando precisa', analista: 'acha a causa e previne', maestro: 'coordena o time',
}
const BASE = '/assets-goxys/goxys/base.webp'

export function GoxyFlip({ mascote, onToque }: { mascote: Mascote; onToque?: number }) {
  const [i, setI] = useState(0)
  const [pulando, setPulando] = useState(0) // muda a cada ciclo: reinicia a animação orquestrada
  const [pronto, setPronto] = useState(false)
  const [pausa, setPausa] = useState(false)
  const reduzir = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const timers = useRef<number[]>([])
  const [hover, setHover] = useState(false)
  const [tilt, setTilt] = useState(0)
  const clicou = useRef(false)


  const img = (k: string) => (k === 'base' ? mascote.base ?? BASE : mascote.agentes[k]?.img ?? mascote.agentes[k]?.poster)
  const imgSm = (k: string) => (k === 'base' ? mascote.base_sm : mascote.agentes[k]?.img_sm)
  const video = (k: string) => (k === 'base' ? undefined : mascote.agentes[k]?.video)
  const disponiveis = ORDEM.filter((k) => !!img(k))

  // pré-carrega as imagens antes do primeiro flip
  useEffect(() => {
    if (disponiveis.length < 2) return
    let vivo = true
    const carregar = (src: string) => new Promise<void>((ok) => { const im = new Image(); im.onload = im.onerror = () => ok(); im.src = src })
    Promise.race([Promise.all(disponiveis.map((k) => carregar(img(k)!))), new Promise((ok) => setTimeout(ok, 5000))]).then(() => vivo && setPronto(true))
    return () => { vivo = false }
  }, [disponiveis.join()])

  /** Agacha, pula, gira no topo (troca o personagem a 90°), cai com um quique. Tudo em 1,1 s. */
  const [ciclo, setCiclo] = useState(0) // reinicia a barra de progresso
  const avancar = (alvo?: number) => {
    const n = Math.max(1, disponiveis.length)
    const prox = (x: number) => (alvo != null ? ((alvo % n) + n) % n : (x + 1) % n)
    setCiclo((c) => c + 1)
    if (reduzir) { setI(prox); return }
    timers.current.forEach(clearTimeout); timers.current = []
    setPulando((x) => x + 1)
    timers.current.push(window.setTimeout(() => setI(prox), 440)) // no topo do pulo, de lado
    timers.current.push(window.setTimeout(() => setPulando(0), 1150))
  }
  useEffect(() => {
    if (!pronto || pausa) return
    const t = window.setInterval(() => avancar(), reduzir ? 8000 : 5500)
    return () => window.clearInterval(t)
  }, [pronto, pausa, disponiveis.length, ciclo])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])
  useEffect(() => { if (onToque) setPausa(false) }, [onToque])

  const [jaClicou, setJaClicou] = useState(() => { try { return sessionStorage.getItem('goxy-clicou') === '1' } catch { return false } })
  const clicar = (alvo?: number) => {
    clicou.current = true
    setJaClicou(true)
    try { sessionStorage.setItem('goxy-clicou', '1') } catch { /* sem storage */ }
    avancar(alvo)
  }

  const k = disponiveis[i % Math.max(1, disponiveis.length)] ?? 'base'
  const proximo = disponiveis[(i + 1) % Math.max(1, disponiveis.length)] ?? 'base'
  const v = video(k)
  const usarVideo = !!v && suportaWebmAlfa()
  const nome = k === 'base' ? 'Goxy' : nomeAgente(k)

  const total = disponiveis.length
  return (
    <div className="relative h-full w-full">
    <div className="absolute inset-0">
    <button type="button" onClick={() => clicar()}
      onMouseEnter={() => { setPausa(true); setHover(true) }} onMouseLeave={() => { setPausa(false); setHover(false); setTilt(0) }}
      onMouseMove={(e) => { if (reduzir) return; const r = e.currentTarget.getBoundingClientRect(); setTilt(((e.clientX - r.left) / r.width - 0.5) * 8) }}
      onFocus={() => setPausa(true)} onBlur={() => setPausa(false)}
      aria-label={`Trocar de agente: próximo é ${proximo === 'base' ? 'o Goxy' : `o ${nomeAgente(proximo)}`}`}
      className="relative flex h-full w-full cursor-pointer items-end justify-center rounded-[40px] pt-8 focus-visible:outline-offset-4" style={{ perspective: 1100 }}>
      {!jaClicou && <span aria-hidden className="anel-chama" style={{ position: 'absolute', bottom: '1%', left: '17%', height: '10%', width: '66%', borderRadius: '50%', border: '2px solid #ffffff' }} />}
      <span aria-hidden key={`s${pulando}`} className={pulando ? 'sombra-pulo' : ''} style={{ position: 'absolute', bottom: '3%', left: '21%', height: '6%', width: '58%', borderRadius: '50%', background: '#163f7a', transform: hover && !reduzir ? 'scaleX(1.08)' : undefined, transition: 'transform 200ms ease-out' }} />
      {/* olhadinha para o mouse: tilt e mini-pulo (fora do pulo-flip para não brigar com ele) */}
      <span className="relative block h-full w-full" style={{ transformOrigin: '50% 100%', transform: hover && !reduzir ? `translateY(-2.5%) rotate(${tilt.toFixed(1)}deg)` : 'none', transition: 'transform 220ms ease-out' }}>
      <span key={`p${pulando}`} className={pulando ? 'pulo-flip relative block h-full w-full' : 'relative block h-full w-full'} style={{ transformOrigin: '50% 100%' }}>
      <span key={reduzir ? `f${k}` : undefined} className={`goxy-anim goxy-parado relative block h-full w-full${reduzir ? ' fade-rm' : ''}`}>
        {usarVideo
          ? <video key={v} src={v} poster={img(k)} autoPlay loop muted playsInline className="pointer-events-none h-full w-full object-contain" />
          : <img key={img(k)} src={img(k) ?? BASE_GOXY} srcSet={srcSetDe(imgSm(k), img(k))} sizes="(min-width: 768px) 460px, 70vw" alt="" draggable={false} className="pointer-events-none h-full w-full object-contain" />}
      </span>
      </span>
      </span>
    </button>
    {/* rótulo fixo */}
    <span aria-live="polite" className="pointer-events-none absolute left-1/2 top-0 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full bg-limao px-3 py-1 text-[13px] font-bold text-azul">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M6 8V2.8a1.2 1.2 0 0 1 2.4 0V7l3.4.7a1.6 1.6 0 0 1 1.2 1.9l-.8 3.6A2 2 0 0 1 10.3 15H8a3 3 0 0 1-2.4-1.2L3.3 10.6a1.2 1.2 0 0 1 1.8-1.6z" /></svg>
      {nome} · {PAPEL[k]}
    </span>
    </div>

    {/* indicador estilo carrossel: anterior, 9 bolinhas, próximo, e a barra até o próximo flip */}
    <div className="carrossel-goxy pointer-events-none absolute left-1/2 top-[calc(100%+18px)] hidden -translate-x-1/2 flex-col items-center md:flex">
    <div className="pointer-events-auto flex items-center justify-center gap-2 opacity-90">
      <button type="button" onClick={() => clicar(i - 1)} aria-label="Agente anterior" className="flex h-7 w-7 items-center justify-center rounded-full text-white opacity-70 ring-1 ring-white hover:opacity-100">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M13 8H3M7 4 3 8l4 4" /></svg>
      </button>
      <div role="tablist" aria-label="Agentes" className="flex items-center gap-2">
        {disponiveis.map((a, j) => {
          const ativo = j === i % Math.max(1, total)
          const nm = a === 'base' ? 'Goxy' : nomeAgente(a)
          return (
            <button key={a} type="button" role="tab" aria-selected={ativo} aria-label={nm} title={nm} onClick={() => clicar(j)}
              className={ativo ? 'h-2 w-2 rounded-full bg-white' : 'h-1.5 w-1.5 rounded-full bg-[#7f9fd0] hover:bg-white'} />
          )
        })}
      </div>
      <button type="button" onClick={() => clicar()} aria-label="Próximo agente" className="flex h-7 w-7 items-center justify-center rounded-full text-white opacity-70 ring-1 ring-white hover:opacity-100">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 8h10M9 4l4 4-4 4" /></svg>
      </button>
    </div>
    <div className="mt-1.5 h-[2px] w-32 overflow-hidden rounded-full bg-[#3a68ad]" aria-hidden>
      <div key={ciclo} className="barra-flip h-full rounded-full bg-[#9db6dd]" style={{ animationDuration: reduzir ? '0s' : '5.5s', animationPlayState: pausa ? 'paused' : 'running' }} />
    </div>
    </div>
    {/* celular: só as setas pequenas, ao lado do Goxy */}
    <button type="button" onClick={() => clicar(i - 1)} aria-label="Agente anterior" className="carrossel-goxy absolute left-[-6px] top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-white opacity-70 ring-1 ring-white md:hidden">
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M13 8H3M7 4 3 8l4 4" /></svg>
    </button>
    <button type="button" onClick={() => clicar()} aria-label="Próximo agente" className="carrossel-goxy absolute right-[-6px] top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-white opacity-70 ring-1 ring-white md:hidden">
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 8h10M9 4l4 4-4 4" /></svg>
    </button>
    </div>
  )
}
