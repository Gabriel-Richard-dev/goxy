// Componentes no estilo Magic UI / Aceternity, escritos aqui (copiar e colar), na paleta do manual Gogroup.
// Todos respeitam prefers-reduced-motion. Sem vidro, sem 3D, sem confete.
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { animate, AnimatePresence, motion, useInView, useReducedMotion } from 'motion/react'
import { cx } from '../../lib'

/* ---------- Animated Beam: feixe ciano -> limão entre dois elementos ---------- */
export function AnimatedBeam({ container, de, para, curva = 0, atraso = 0, duracao = 3, reverso = false, largura = 2 }: {
  container: RefObject<HTMLElement>; de: RefObject<HTMLElement>; para: RefObject<HTMLElement>
  curva?: number; atraso?: number; duracao?: number; reverso?: boolean; largura?: number
}) {
  const id = useId().replace(/:/g, '')
  const reduzir = useReducedMotion()
  const [d, setD] = useState('')
  const [tam, setTam] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const calc = () => {
      const c = container.current?.getBoundingClientRect(), a = de.current?.getBoundingClientRect(), b = para.current?.getBoundingClientRect()
      if (!c || !a || !b) return
      setTam({ w: c.width, h: c.height })
      const x1 = a.left - c.left + a.width / 2, y1 = a.top - c.top + a.height / 2
      const x2 = b.left - c.left + b.width / 2, y2 = b.top - c.top + b.height / 2
      setD(`M ${x1},${y1} Q ${(x1 + x2) / 2},${(y1 + y2) / 2 - curva} ${x2},${y2}`)
    }
    // o ref do contêiner (ancestral) só é ligado depois do efeito do filho: espera um quadro
    const ro = new ResizeObserver(calc)
    const raf = requestAnimationFrame(() => { calc(); if (container.current) ro.observe(container.current) })
    window.addEventListener('resize', calc)
    return () => { cancelAnimationFrame(raf); ro.disconnect(); window.removeEventListener('resize', calc) }
  }, [container, de, para, curva])
  const ini = reverso ? ['90%', '100%'] : ['10%', '0%']
  const fim = reverso ? ['-10%', '0%'] : ['110%', '100%']
  return (
    <svg className="pointer-events-none absolute left-0 top-0" width={tam.w} height={tam.h} viewBox={`0 0 ${tam.w} ${tam.h}`} aria-hidden>
      <path d={d} stroke="#5a85c4" strokeWidth={largura} strokeLinecap="round" fill="none" />
      {!reduzir && (
        <path d={d} stroke={`url(#${id})`} strokeWidth={largura + 1} strokeLinecap="round" fill="none" />
      )}
      <defs>
        <motion.linearGradient id={id} gradientUnits="userSpaceOnUse"
          initial={{ x1: ini[0], x2: ini[1], y1: '0%', y2: '0%' }}
          animate={{ x1: [ini[0], fim[0]], x2: [ini[1], fim[1]] }}
          transition={{ delay: atraso, duration: duracao, ease: [0.16, 1, 0.3, 1], repeat: Infinity, repeatDelay: 0.4 }}>
          <stop stopColor="#3dbfef" stopOpacity="0" />
          <stop stopColor="#3dbfef" />
          <stop offset="32.5%" stopColor="#d7d900" />
          <stop offset="100%" stopColor="#d7d900" stopOpacity="0" />
        </motion.linearGradient>
      </defs>
    </svg>
  )
}

/* ---------- Animated List: itens entram um a um no topo ---------- */
export function AnimatedList({ itens, intervalo = 1600, visiveis = 4, render }: { itens: { id: string }[]; intervalo?: number; visiveis?: number; render: (i: any) => ReactNode }) {
  const reduzir = useReducedMotion()
  const [n, setN] = useState(reduzir ? itens.length : Math.min(visiveis, itens.length))
  useEffect(() => {
    if (reduzir || !itens.length) return
    const t = setInterval(() => setN((x) => (x >= itens.length ? Math.min(visiveis, itens.length) : x + 1)), intervalo)
    return () => clearInterval(t)
  }, [itens.length, intervalo, reduzir, visiveis])
  const mostrar = itens.slice(0, n).reverse().slice(0, visiveis)
  return (
    <ul className="flex flex-col gap-2">
      <AnimatePresence initial={false}>
        {mostrar.map((it) => (
          <motion.li key={it.id} layout initial={{ opacity: 0, y: -14, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 350, damping: 34 }}>
            {render(it)}
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  )
}

/* ---------- Shimmer Button: azul com um brilho que corre ---------- */
export function ShimmerButton({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} className={cx('shimmer relative inline-flex items-center gap-3 overflow-hidden rounded-full bg-azul py-2 pl-7 pr-2 text-[17px] font-bold text-white transition hover:brightness-110', className)}>
      <span className="relative z-10 inline-flex items-center gap-3">{children}</span>
    </a>
  )
}

/* ---------- Dot Pattern com fade radial ---------- */
export function DotPattern({ className }: { className?: string }) {
  const id = useId().replace(/:/g, '')
  return (
    <svg aria-hidden className={cx('pointer-events-none absolute inset-0 h-full w-full', className)}
      style={{ maskImage: 'radial-gradient(ellipse at center, black 30%, transparent 75%)', WebkitMaskImage: 'radial-gradient(ellipse at center, black 30%, transparent 75%)' }}>
      <defs><pattern id={id} width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.3" fill="currentColor" /></pattern></defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}

/* ---------- Number Ticker: conta até o valor quando entra na tela ---------- */
/** Recebe um texto ("R$ 527,24", "20/20", "124 s") e anima o primeiro número, preservando o resto. */
const jaContou = new Set<string>()
export function NumberTicker({ texto, className, chave }: { texto: string; className?: string; chave?: string }) {
  const unica = chave ? `${chave}:${texto}` : null
  const ref = useRef<HTMLSpanElement>(null)
  const visto = useInView(ref, { once: true })
  const reduzir = useReducedMotion()
  const m = texto.match(/\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?/)
  const pular = !!unica && jaContou.has(unica)
  const [atual, setAtual] = useState<string>(m && !reduzir && !pular ? texto.replace(m[0], m[0].replace(/\d/g, '0')) : texto)
  useEffect(() => {
    if (!m || reduzir || pular) { setAtual(texto); return }
    if (!visto) return
    if (unica) jaContou.add(unica)
    const bruto = m[0]
    const dec = bruto.includes(',') ? bruto.split(',')[1].length : 0
    const alvo = Number(bruto.replace(/\./g, '').replace(',', '.'))
    const ctl = animate(0, alvo, {
      duration: 1.4, ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setAtual(texto.replace(bruto, v.toLocaleString('pt-BR', { minimumFractionDigits: dec, maximumFractionDigits: dec }))),
    })
    return () => ctl.stop()
  }, [visto, texto, reduzir])
  return <span ref={ref} className={cx('num inline-block tabular-nums', className)} style={{ minWidth: `${texto.length * 0.62}em` }}>{atual}</span>
}

/* ---------- Marquee: faixa horizontal lenta que pausa no hover ---------- */
export function Marquee({ children, duracao = 60 }: { children: ReactNode; duracao?: number }) {
  return (
    <div className="marquee group flex overflow-hidden" style={{ ['--dur' as string]: `${duracao}s` }}>
      {[0, 1].map((k) => (
        <div key={k} aria-hidden={k === 1} className="marquee-trilho flex shrink-0 items-center gap-3 pr-3 group-hover:[animation-play-state:paused]">{children}</div>
      ))}
    </div>
  )
}

/* ---------- Bento Grid ---------- */
export function BentoGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('grid auto-rows-[minmax(150px,auto)] gap-4 md:grid-cols-3', className)}>{children}</div>
}
export function BentoCard({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('relative overflow-hidden rounded-col p-6', className)}>{children}</div>
}

/* ---------- Border Beam: um feixe que contorna a borda ---------- */
export function BorderBeam({ cor = '#2659a5', duracao = 6, vezes = 'infinite', largura = 2 }: { cor?: string; duracao?: number; vezes?: number | 'infinite'; largura?: number }) {
  return (
    <span aria-hidden className="border-beam pointer-events-none absolute inset-0 rounded-[inherit]"
      style={{ ['--bb-cor' as string]: cor, ['--bb-dur' as string]: `${duracao}s`, ['--bb-vezes' as string]: String(vezes), ['--bb-larg' as string]: `${largura}px` }} />
  )
}
