import { useMemo, useState } from 'react'
import type { Case, Mascote } from '../types'
import { cx, hora, titulo } from '../lib'
import { api } from '../api'

const ORDEM: Record<string, number> = { em_andamento: 0, aguardando_aprovacao: 1, novo: 2, humano: 3, rejeitado: 4, resolvido: 5, erro: 6 }
const COR: Record<string, string> = {
  aguardando_aprovacao: '#f8ae13', erro: '#e5273c', em_andamento: '#3dbfef', novo: '#aeb5c0',
  humano: '#2659a5', resolvido: '#2659a5', rejeitado: '#e5273c',
}

type Grupo = { k: string; t: string; l: Case[]; aberto: boolean }

export function Fila({ cases, selecionado, onSelecionar }: { cases: Case[]; selecionado: string | null; mascote?: Mascote; onSelecionar: (id: string) => void }) {
  const lista = useMemo(() => [...cases].sort((a, b) => (ORDEM[a.status] ?? 9) - (ORDEM[b.status] ?? 9) || b.criado_em.localeCompare(a.criado_em)), [cases])
  const [abertos, setAbertos] = useState<Record<string, boolean>>({ voce: true, andamento: true, pessoa: false, resolvidos: false })
  const grupos: Grupo[] = useMemo(() => [
    { k: 'voce', t: 'Precisa de você', l: lista.filter((c) => ['aguardando_aprovacao', 'erro'].includes(c.status)), aberto: true },
    { k: 'andamento', t: 'Em andamento', l: lista.filter((c) => ['em_andamento', 'novo'].includes(c.status) && !c.responsavel), aberto: true },
    { k: 'pessoa', t: 'Com uma pessoa', l: lista.filter((c) => c.status === 'humano' || (c.responsavel && !['resolvido', 'rejeitado', 'aguardando_aprovacao'].includes(c.status))), aberto: false },
    { k: 'resolvidos', t: 'Resolvidos', l: lista.filter((c) => ['resolvido', 'rejeitado'].includes(c.status)), aberto: false },
  ], [lista])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <h2 className="px-5 pb-1 pt-5 text-[15px] font-semibold text-texto">Fila <span className="num font-normal text-suave">{cases.length}</span></h2>
      <div className="rolagem flex-1 overflow-y-auto px-2 pb-4">
        {lista.length === 0 && (
          <p className="px-3 py-8 text-center text-[14px] text-suave">Nenhum caso esperando. O time está em dia.</p>
        )}
        {grupos.filter((g) => g.l.length).map((g) => {
          const aberto = abertos[g.k] ?? g.aberto
          return (
            <section key={g.k} className="pt-3">
              <button onClick={() => setAbertos((a) => ({ ...a, [g.k]: !aberto }))} aria-expanded={aberto}
                className="flex w-full items-center gap-1.5 px-3 pb-1 text-left text-[13px] font-semibold text-suave hover:text-texto">
                <svg width="12" height="12" viewBox="0 0 16 16" className={cx('shrink-0', aberto && 'rotate-90')} fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden><path d="m6 4 4 4-4 4" /></svg>
                {g.t}<span className="num font-normal">{g.l.length}</span>
              </button>
              {aberto && (
                <ul className="space-y-0.5">
                  {g.l.map((c) => {
                    const ativo = c.id === selecionado
                    const meta = [c.cliente_nome?.split(' ')[0], c.order_id, hora(c.criado_em).slice(0, 5)].filter(Boolean).join(' · ')
                    return (
                      <li key={c.id}>
                        <div role="button" tabIndex={0} aria-current={ativo} onClick={() => onSelecionar(c.id)}
                          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelecionar(c.id))}
                          className={cx('flex cursor-pointer items-start gap-2.5 rounded-[12px] px-3 py-2', ativo ? 'bg-marca-suave' : 'hover:bg-sup2')}>
                          <span className="mt-[7px] h-2 w-2 shrink-0 rounded-full" style={{ background: COR[c.status] ?? '#aeb5c0' }} aria-hidden />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[14px] font-semibold text-texto">{titulo(c)}</span>
                            <span className="block truncate text-[12.5px] text-suave">{meta}</span>
                          </span>
                          {c.status === 'novo' && (
                            <button onClick={(e) => { e.stopPropagation(); api.post(`/api/goxys/casos/${c.id}/processar`).catch(() => {}) }}
                              className="shrink-0 text-[12.5px] font-semibold text-estrutura hover:underline">Processar</button>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
