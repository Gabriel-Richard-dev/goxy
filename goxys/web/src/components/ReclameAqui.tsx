import { useEffect, useState } from 'react'
import type { Case, Dict } from '../types'
import { api } from '../api'
import { useGoxy } from '../store'

const cacheRA: Record<string, string | null> = {}

/** Link "Ver no Reclame Aqui" para casos do canal reclame_aqui. */
export function LinkReclameAqui({ c }: { c: Case }) {
  const { s } = useGoxy()
  const [id, setId] = useState<string | null>(cacheRA[c.id] ?? null)
  // o id pode vir dos eventos ra.nova / ra.publicada deste caso
  const doEvento = (s.eventos[c.id] ?? []).map((e) => e.type.startsWith('ra.') ? (e.data?.reclamacao_id as string | undefined) : undefined).find(Boolean)
  useEffect(() => {
    if (c.canal !== 'reclame_aqui' || doEvento || cacheRA[c.id] !== undefined) return
    api.get<Dict[]>(`/api/reclameaqui/reclamacoes?case_id=${encodeURIComponent(c.id)}`)
      .then((l) => { const r = Array.isArray(l) ? l[0]?.reclamacao_id ?? null : null; cacheRA[c.id] = r; setId(r) })
      .catch(() => { cacheRA[c.id] = null })
  }, [c.id, c.canal, doEvento])
  const rid = doEvento ?? id
  if (c.canal !== 'reclame_aqui' || !rid) return null
  return (
    <a href={`/reclameaqui/reclamacao/${encodeURIComponent(rid)}`} target="_blank" rel="noreferrer"
      className="rounded-full px-3 py-1 text-[14px] font-semibold text-estrutura hover:bg-sup">Ver no Reclame Aqui</a>
  )
}

/** Bloco pequeno de reputação em Resultados. */
export function ReputacaoRA() {
  const [r, setR] = useState<Dict | null>(null)
  useEffect(() => { api.get<Dict>('/api/reclameaqui/reputacao?empresa=Gocase').then(setR).catch(() => {}) }, [])
  if (!r || r.indice_reputacao == null) return null
  const pct = (x: unknown) => (typeof x === 'number' ? `${Math.round(x <= 1 ? x * 100 : x)}%` : '—')
  const itens: [string, string][] = [
    ['respondidas', pct(r.pct_respondidas)],
    ['resolvidas', pct(r.pct_resolvidas)],
    ['até a 1ª resposta', singular(r.tempo_medio_primeira_resposta_txt) ?? (r.tempo_medio_primeira_resposta_h != null ? `${Math.round(r.tempo_medio_primeira_resposta_h)} h` : '—')],
  ]
  return (
    <section className="mt-6 flex flex-wrap items-center gap-x-10 gap-y-4 rounded-col bg-sup px-7 py-5">
      <div>
        <p className="text-[14px] font-semibold text-texto">Reputação no Reclame Aqui</p>
        <p className="num text-[36px] font-extrabold leading-none">{Number(r.indice_reputacao).toFixed(1).replace('.', ',')}<span className="text-[16px] font-semibold text-suave">/10</span></p>
        {r.classificacao && <p className="text-[14px] font-semibold text-estrutura">{r.classificacao}</p>}
      </div>
      {itens.map(([k, v]) => (
        <div key={k}>
          <p className="num text-[24px] font-bold leading-none">{v}</p>
          <p className="mt-1 text-[13.5px] text-suave">{k}</p>
        </div>
      ))}
      {r.respostas_goxys ? <p className="text-[13.5px] text-suave">{r.respostas_goxys} {r.respostas_goxys === 1 ? 'resposta publicada' : 'respostas publicadas'} pelo Goxy</p> : null}
    </section>
  )
}

/** "1 dias e 4 h" -> "1 dia e 4 h" (e o mesmo para horas/minutos). */
function singular(t?: string | null): string | null {
  if (!t) return null
  return t.replace(/\b1 dias\b/g, '1 dia').replace(/\b1 horas\b/g, '1 hora').replace(/\b1 minutos\b/g, '1 minuto')
}
