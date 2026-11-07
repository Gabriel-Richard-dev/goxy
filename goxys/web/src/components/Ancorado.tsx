import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** Popover em portal (document.body) com position: fixed, ancorado no elemento pai do marcador.
 *  Alinha à direita (ou esquerda) do âncora, nunca sai da tela e vira para cima se faltar espaço embaixo. */
export function Ancorado({ largura, minLargura = 0, alinhar = 'direita', altura = 320, children, onAncora }: { largura: number | 'ancora'; minLargura?: number; alinhar?: 'direita' | 'esquerda'; altura?: number; children: (maxAltura: number) => ReactNode; onAncora?: (el: HTMLElement | null) => void }) {
  const marcador = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ top?: number; bottom?: number; left: number; max: number; w: number } | null>(null)
  useLayoutEffect(() => {
    const calc = () => {
      const el = marcador.current?.parentElement
      if (!el) return
      onAncora?.(el)
      const r = el.getBoundingClientRect()
      const baixo = window.innerHeight - r.bottom - 12
      const cima = r.top - 12
      const paraCima = baixo < altura && cima > baixo
      const w = Math.min(window.innerWidth - 16, Math.max(minLargura, largura === 'ancora' ? r.width : largura))
      let left = alinhar === 'direita' ? r.right - w : r.left
      left = Math.max(8, Math.min(left, window.innerWidth - w - 8))
      setPos(paraCima ? { bottom: window.innerHeight - r.top + 6, left, max: cima - 6, w } : { top: r.bottom + 6, left, max: baixo - 6, w })
    }
    calc()
    window.addEventListener('resize', calc)
    window.addEventListener('scroll', calc, true)
    return () => { window.removeEventListener('resize', calc); window.removeEventListener('scroll', calc, true) }
  }, [largura, alinhar, altura])
  return (
    <>
      <span ref={marcador} hidden />
      {pos && createPortal(
        <div style={{ position: 'fixed', top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.w, zIndex: 70 }}>{children(pos.max)}</div>,
        document.body,
      )}
    </>
  )
}
