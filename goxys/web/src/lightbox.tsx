import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

// Lightbox simples: abre qualquer miniatura de foto em tela cheia.
export interface ImagemLB { src: string; legenda?: string; alt?: string }
const Ctx = createContext<(imgs: ImagemLB[], i?: number) => void>(() => {})
export const useLightbox = () => useContext(Ctx)

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [estado, setEstado] = useState<{ imgs: ImagemLB[]; i: number } | null>(null)
  const voltar = useRef<HTMLElement | null>(null)
  const abrir = useCallback((imgs: ImagemLB[], i = 0) => {
    if (!imgs.length) return
    voltar.current = document.activeElement as HTMLElement | null
    setEstado({ imgs, i })
  }, [])
  const fechar = useCallback(() => { setEstado(null); setTimeout(() => voltar.current?.focus(), 0) }, [])
  return (
    <Ctx.Provider value={abrir}>
      {children}
      {estado && <Lightbox imgs={estado.imgs} i={estado.i} setI={(i) => setEstado({ ...estado, i })} onFechar={fechar} />}
    </Ctx.Provider>
  )
}

function Lightbox({ imgs, i, setI, onFechar }: { imgs: ImagemLB[]; i: number; setI: (i: number) => void; onFechar: () => void }) {
  const caixa = useRef<HTMLDivElement>(null)
  const fecharBtn = useRef<HTMLButtonElement>(null)
  const n = imgs.length
  useEffect(() => {
    fecharBtn.current?.focus()
    const tecla = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onFechar() }
      else if (e.key === 'ArrowRight' && n > 1) setI((i + 1) % n)
      else if (e.key === 'ArrowLeft' && n > 1) setI((i - 1 + n) % n)
      else if (e.key === 'Tab') {
        // foco preso dentro do lightbox
        const f = caixa.current?.querySelectorAll<HTMLElement>('button')
        if (!f?.length) return
        const prim = f[0], ult = f[f.length - 1]
        if (e.shiftKey && document.activeElement === prim) { e.preventDefault(); ult.focus() }
        else if (!e.shiftKey && document.activeElement === ult) { e.preventDefault(); prim.focus() }
      }
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  }, [i, n, onFechar, setI])
  const img = imgs[i]
  return (
    <div ref={caixa} role="dialog" aria-modal="true" aria-label={img.legenda ?? 'Imagem em tela cheia'} className="lightbox-in fixed inset-0 z-[60] flex flex-col items-center justify-center bg-black/50 p-4" onClick={onFechar}>
      <button ref={fecharBtn} onClick={onFechar} aria-label="Fechar" className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded-full bg-white text-azul hover:bg-[#eef1f6]">
        <svg width="18" height="18" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m4 4 8 8M12 4l-8 8" /></svg>
      </button>
      {n > 1 && (
        <>
          <button onClick={(e) => { e.stopPropagation(); setI((i - 1 + n) % n) }} aria-label="Imagem anterior" className="absolute left-4 top-1/2 flex h-12 w-12 -translate-y-1/2 items-center justify-center rounded-full bg-white text-azul hover:bg-[#eef1f6]">
            <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m10 3-5 5 5 5" /></svg>
          </button>
          <button onClick={(e) => { e.stopPropagation(); setI((i + 1) % n) }} aria-label="Próxima imagem" className="absolute right-4 top-1/2 flex h-12 w-12 -translate-y-1/2 items-center justify-center rounded-full bg-white text-azul hover:bg-[#eef1f6]">
            <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m6 3 5 5-5 5" /></svg>
          </button>
        </>
      )}
      <img key={img.src} src={img.src} alt={img.alt ?? img.legenda ?? ''} onClick={(e) => e.stopPropagation()} className="max-h-[90vh] max-w-[90vw] rounded-[18px] bg-white object-contain" style={{ maxHeight: 'calc(90vh - 48px)' }} />
      {(img.legenda || n > 1) && (
        <p className="mt-3 text-center text-[14.5px] text-white/85" onClick={(e) => e.stopPropagation()}>
          {img.legenda}{n > 1 ? <span className="num ml-2 text-white/60">{i + 1}/{n}</span> : null}
        </p>
      )}
    </div>
  )
}

/** Miniatura clicável que abre no lightbox. */
export function Miniatura({ src, alt, legenda, grupo, indice = 0, className }: { src: string; alt?: string; legenda?: string; grupo?: ImagemLB[]; indice?: number; className?: string }) {
  const abrir = useLightbox()
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); abrir(grupo ?? [{ src, legenda, alt }], grupo ? indice : 0) }}
      aria-label={`Ver ${alt ?? 'foto'} em tela cheia`} className="shrink-0 cursor-zoom-in rounded-[12px] focus-visible:outline-offset-2">
      <img src={src} alt={alt ?? ''} className={className} />
    </button>
  )
}
