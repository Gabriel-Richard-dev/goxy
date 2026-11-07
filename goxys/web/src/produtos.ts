import { useEffect, useState } from 'react'
import type { Dict } from './types'

// Fotos REAIS de catálogo da Gocase (assets/produtos). Servem como "foto do produto do pedido",
// nunca como evidência de avaria. Sem o SKU no manifesto, nada de imagem.
export interface Produtos { familias: Record<string, { img: string; nome: string; url_origem?: string }>; skus: Record<string, string> }
export interface FotoProduto { img: string; nome: string; slug: string }

let cache: Promise<Produtos | null> | null = null
function carregar(): Promise<Produtos | null> {
  if (!cache) {
    cache = fetch('/assets-goxys/produtos/manifest.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((j: Dict | null) => (j?.familias && j?.skus ? (j as Produtos) : null))
      .catch(() => null)
  }
  return cache
}

export function useProdutos(): Produtos | null {
  const [p, setP] = useState<Produtos | null>(null)
  useEffect(() => { let vivo = true; carregar().then((x) => vivo && setP(x)); return () => { vivo = false } }, [])
  return p
}

export function fotoDoSku(p: Produtos | null, sku?: string | null): FotoProduto | null {
  if (!p || !sku) return null
  const slug = p.skus[sku]
  const f = slug ? p.familias[slug] : undefined
  return f?.img ? { img: f.img, nome: f.nome, slug } : null
}

export function familiasPorPalavra(p: Produtos | null, palavras: string[]): FotoProduto[] {
  if (!p) return []
  const out: FotoProduto[] = []
  for (const w of palavras) {
    const e = Object.entries(p.familias).find(([slug]) => slug.includes(w) && !out.some((o) => o.slug === slug))
    if (e?.[1].img) out.push({ img: e[1].img, nome: e[1].nome, slug: e[0] })
  }
  return out
}
