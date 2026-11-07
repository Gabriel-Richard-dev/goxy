import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx, TOM_CLASSE, type Tom } from '../lib'

export function Pill({ tom = 'neutro', children, className }: { tom?: Tom; children: ReactNode; className?: string }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[13px] font-medium', TOM_CLASSE[tom], className)}>
      {children}
    </span>
  )
}

export function NivelBadge({ nivel, grande }: { nivel?: string | null; grande?: boolean }) {
  if (!nivel) return null
  const cor = nivel === 'A' || nivel === 'B' ? 'bg-azul text-white' : nivel === 'C' ? 'bg-laranja text-[#333333]' : 'bg-carmim text-white'
  return (
    <span
      title={`Nível ${nivel}`}
      className={cx('inline-flex items-center justify-center rounded-[10px] font-extrabold num', cor, grande ? 'h-10 w-10 text-xl' : 'h-6 w-6 text-[13px]')}
    >
      {nivel}
    </span>
  )
}

type Variante = 'marca' | 'contorno' | 'perigo' | 'fantasma'
export function Botao({ variante = 'contorno', className, children, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variante?: Variante }) {
  const v: Record<Variante, string> = {
    marca: 'bg-marca text-white hover:brightness-110',
    contorno: 'bg-sup text-texto border border-borda hover:border-estrutura',
    perigo: 'bg-sup text-critico border border-borda hover:border-critico',
    fantasma: 'text-estrutura hover:bg-sup2',
  }
  return (
    <button
      {...p}
      className={cx('inline-flex items-center justify-center gap-2 rounded-full px-4 py-2 text-[14px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-50', v[variante], className)}
    >
      {children}
    </button>
  )
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx('font-mono text-[12.5px]', className)}>{children}</span>
}

export function IconeStatus({ status, size = 16 }: { status: string; size?: number }) {
  const p = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }
  switch (status) {
    case 'resolvido':
    case 'ok':
      return <svg {...p}><path d="m3 8.5 3 3 7-7" /></svg>
    case 'aguardando_aprovacao':
    case 'esperando':
      return <svg {...p}><circle cx="8" cy="8" r="6" /><path d="M8 5v3l2 1.5" /></svg>
    case 'rejeitado':
    case 'falhou':
    case 'erro':
      return <svg {...p}><path d="m4 4 8 8M12 4l-8 8" /></svg>
    case 'alerta':
      return <svg {...p}><path d="M8 2 1.5 13.5h13z" /><path d="M8 6.5v3M8 11.5v.01" /></svg>
    case 'humano':
      return <svg {...p}><circle cx="8" cy="5" r="2.5" /><path d="M3 14c.5-3 2.5-4.5 5-4.5s4.5 1.5 5 4.5" /></svg>
    case 'em_andamento':
    case 'rodando':
      return <svg {...p} className="animate-spin [animation-duration:1.4s]"><path d="M8 2a6 6 0 1 1-6 6" /></svg>
    default:
      return <svg {...p}><circle cx="8" cy="8" r="2.5" fill="currentColor" /></svg>
  }
}

export function IconeCanal({ canal, size = 16 }: { canal: string; size?: number }) {
  const p = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }
  switch (canal) {
    case 'whatsapp': return <svg {...p}><path d="M2.5 13.5 3.4 11A5.6 5.6 0 1 1 5 12.6z" /></svg>
    case 'email': return <svg {...p}><rect x="2" y="3.5" width="12" height="9" rx="2" /><path d="m2.5 4.5 5.5 4 5.5-4" /></svg>
    case 'telegram': return <svg {...p}><path d="M14 2.5 1.8 7.3l4.2 1.5L12 4.5 7.5 9.6l4.3 3.4z" /></svg>
    case 'slack': return <svg {...p}><path d="M6.2 2.5v11M9.8 2.5v11M2.5 6.2h11M2.5 9.8h11" /></svg>
    case 'reclame_aqui': return <svg {...p}><path d="M2.5 6v4h2.5l4.5 3V3L5 6z" /><path d="M12 6.5a2.5 2.5 0 0 1 0 3" /></svg>
    default: return <svg {...p}><rect x="2" y="2.5" width="12" height="9" rx="2" /><path d="M6 14h4" /></svg>
  }
}

export function Vazio({ titulo, children }: { titulo: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <p className="text-[15px] font-semibold">{titulo}</p>
      {children && <div className="text-[14px] text-suave">{children}</div>}
    </div>
  )
}

/** Logo do Goxy: símbolo em <img> + wordmark em HTML (Poppins 800, "X" na cor da marca). */
export function Logo({ size = 32, claro }: { size?: number; claro?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <img src="/app/goxys-simbolo.svg" alt="" width={size} height={size} style={{ width: size, height: size }} />
      <span className={cx('text-[22px] font-extrabold tracking-[-0.02em]', claro ? 'text-white' : 'text-azul dark:text-white')}>Go<span className={claro ? 'text-limao' : 'text-rosa'}>x</span>y</span>
    </span>
  )
}

/** Etiqueta do manual Gogroup: pílula limão com texto azul (rótulo de seção). */
export function Etiqueta({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx('inline-flex items-center rounded-full bg-limao px-3.5 py-1 text-[12.5px] font-bold uppercase tracking-[0.06em] text-azul', className)}>{children}</span>
}

/** Botão circular com seta, como no manual. */
export function SetaCirculo({ claro, size = 36 }: { claro?: boolean; size?: number }) {
  return (
    <span aria-hidden className={cx('inline-flex shrink-0 items-center justify-center rounded-full', claro ? 'bg-white text-azul' : 'bg-azul text-white')} style={{ width: size, height: size }}>
      <svg width={size * 0.45} height={size * 0.45} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
    </span>
  )
}
