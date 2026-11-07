import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Case, ToolCall } from '../types'
import { brl, cx, hora, NIVEL } from '../lib'
import { corAgente, nomeAgente } from '../mascot'
import { Mono, NivelBadge, Pill } from './ui'
import { Comparacao, extrairComparacao } from './Comparacao'
import { acaoLegivel, AvisoPortal, Evidencias, usouPortal } from './CasoAoVivo'

export function Gaveta({ c, onFechar }: { c: Case; onFechar: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.focus()
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onFechar()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onFechar])

  const par = extrairComparacao(c)
  const d = c.decisao ?? {}
  const v = c.veracidade ?? {}
  const t = c.triagem ?? {}
  const sinais: string[] = Array.isArray(d.sinais) ? d.sinais.map(String) : d.sinais ? Object.entries(d.sinais).flatMap(([k, x]): string[] => {
    const nome = k.replace(/_/g, ' ')
    if (x == null || x === false) return []
    if (x === true) return [nome]
    if (typeof x === 'number' || typeof x === 'string') return [`${nome}: ${typeof x === 'number' ? x.toLocaleString('pt-BR') : x}`]
    if (Array.isArray(x)) return x.length ? [`${nome}: ${x.join(', ')}`] : []
    return []
  }) : []
  const revisor = typeof d.revisor === 'string' ? d.revisor : d.revisor?.parecer

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button aria-label="Fechar detalhe" className="absolute inset-0 bg-black/50" onClick={onFechar} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Detalhes do caso"
        className="gaveta-in rolagem relative h-full w-full max-w-[720px] overflow-y-auto rounded-l-gaveta bg-fundo shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] outline-none">
        <header className="sticky top-0 z-10 flex items-center gap-3 bg-fundo px-8 pb-4 pt-7">
          <div className="min-w-0 flex-1">
            <h2 className="text-[22px] font-extrabold">Detalhes do caso</h2>
            <p className="text-[14px] text-suave">Caso <Mono>{c.id}</Mono>{c.order_id && <> do pedido <Mono>{c.order_id}</Mono></>}</p>
          </div>
          <button onClick={onFechar} className="flex h-10 w-10 items-center justify-center rounded-full bg-sup2 hover:bg-borda" aria-label="Fechar">
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </header>

        <div className="space-y-7 px-8 pb-12">
          {par && <Comparacao c={c} par={par} />}

          <Secao titulo="Nível de autonomia aplicado">
            {d.nivel ? (
              <div className="rounded-card bg-sup p-4">
                <div className="flex items-center gap-3">
                  <NivelBadge nivel={d.nivel} grande />
                  <div>
                    <p className="text-[16px] font-semibold">{NIVEL[d.nivel]?.desc}: {acaoLegivel(d.acao)}{d.valor ? ` de ${brl(d.valor)}` : ''}</p>
                    {d.politica_versao && <p className="text-[13px] text-suave">Política <Mono>{d.politica_versao}</Mono></p>}
                  </div>
                </div>
                {d.motivo && <p className="mt-3 text-[14.5px]">{d.motivo}</p>}
                {d.justificativa && <p className="mt-2 text-[14px] leading-relaxed text-suave">{d.justificativa}</p>}
                {d.sinais?.custo_erro?.leitura && <p className="mt-2 text-[14px]"><span className="text-suave">Custo de errar: </span>{d.sinais.custo_erro.leitura.replace(/ · /g, '; ')}</p>}
                {sinais.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{sinais.map((s) => <Pill key={s} tom="estrutura">{s}</Pill>)}</div>}
                {revisor && <p className="mt-3 text-[13.5px] text-suave">Revisor: {revisor}</p>}
              </div>
            ) : <p className="text-[14px] text-suave">A política ainda não decidiu este caso.</p>}
          </Secao>

          {(v.score != null || v.evidencias?.length) && (
            <Secao titulo="Evidências">
              {v.score != null && (
                <div className="mb-3 flex items-center gap-3">
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-sup2">
                    <div className="h-full rounded-full" style={{ width: `${Math.min(100, v.score)}%`, background: v.score >= 70 ? 'var(--ok)' : v.score >= 40 ? 'var(--espera)' : 'var(--critico)' }} />
                  </div>
                  <span className="num text-[15px] font-bold">{Math.round(v.score)}/100</span>
                  {v.faixa && <span className="text-[13px] text-suave">{String(v.faixa).replace(/_/g, ' ')}</span>}
                </div>
              )}
              {v.evidencias?.length > 0 && <Evidencias itens={v.evidencias} />}
            </Secao>
          )}

          {(c.dossie?.rastreio || usouPortal(c)) && (
            <Secao titulo="Rastreio">
              {usouPortal(c) && <div className="-mx-3 mb-2"><AvisoPortal c={c} /></div>}
              <Rastreio c={c} />
            </Secao>
          )}

          {Object.keys(t).length > 0 && (
            <Secao titulo="Triagem">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[14px] sm:grid-cols-3">
                {Object.entries(t).map(([k, x]) => (
                  <div key={k}><dt className="text-[12.5px] text-suave">{k.replace(/_/g, ' ')}</dt><dd className="font-medium">{typeof x === 'boolean' ? (x ? 'sim' : 'não') : typeof x === 'number' && x <= 1 ? `${Math.round(x * 100)}%` : String(x)}</dd></div>
                ))}
              </dl>
            </Secao>
          )}

          <Secao titulo={`Log de ferramentas (${c.acoes?.length ?? 0})`}>
            {c.acoes?.length ? <LogFerramentas acoes={c.acoes} /> : <p className="text-[14px] text-suave">Nenhuma ferramenta chamada ainda.</p>}
          </Secao>

          <Secao titulo="Uso de IA">
            <div className="grid grid-cols-3 gap-3">
              <Numero rotulo="chamadas" valor={String(c.llm_uso?.chamadas ?? 0)} />
              <Numero rotulo="tokens" valor={((c.llm_uso?.tokens_in ?? 0) + (c.llm_uso?.tokens_out ?? 0)).toLocaleString('pt-BR')} sub={`${(c.llm_uso?.tokens_in ?? 0).toLocaleString('pt-BR')} entrada, ${(c.llm_uso?.tokens_out ?? 0).toLocaleString('pt-BR')} saída`} />
              <Numero rotulo="custo" valor={`US$ ${(c.llm_uso?.usd ?? 0).toFixed(4)}`} />
            </div>
          </Secao>
        </div>
      </div>
    </div>
  )
}

function Secao({ titulo, children }: { titulo: string; children: ReactNode }) {
  return <section><h3 className="mb-3 text-[16px] font-semibold">{titulo}</h3>{children}</section>
}

function Numero({ rotulo, valor, sub }: { rotulo: string; valor: string; sub?: string }) {
  return (
    <div className="rounded-card bg-sup p-4">
      <p className="num text-[22px] font-bold leading-tight">{valor}</p>
      <p className="text-[13px] text-suave">{rotulo}</p>
      {sub && <p className="num mt-1 text-[12px] text-suave">{sub}</p>}
    </div>
  )
}

function Rastreio({ c }: { c: Case }) {
  const r = c.dossie?.rastreio ?? {}
  const eventos: any[] = r.eventos ?? []
  return (
    <div className="rounded-card bg-sup p-4 text-[14px]">
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {r.transportadora && <span><span className="text-suave">Transportadora</span> <b>{r.transportadora}</b></span>}
        {r.codigo_rastreio && <span><span className="text-suave">Código</span> <Mono>{r.codigo_rastreio}</Mono></span>}
        {r.status && <span><span className="text-suave">Status</span> <b>{r.status}</b></span>}
        {r.dias_atraso ? <span className="font-semibold text-critico">{r.dias_atraso} dias de atraso</span> : null}
      </div>
      {eventos.length > 0 && (
        <ol className="mt-3 space-y-1">
          {eventos.map((e, i) => <li key={i} className="flex gap-3"><span className="num w-36 shrink-0 text-suave">{e.data_hora}</span><span>{e.status}</span><span className="text-suave">{e.local}</span></li>)}
        </ol>
      )}
      {c.dossie?.inconsistencias?.length > 0 && (
        <ul className="mt-3 space-y-1">{c.dossie.inconsistencias.map((x: string) => <li key={x} className="text-espera-texto">{x}</li>)}</ul>
      )}
    </div>
  )
}

function LogFerramentas({ acoes }: { acoes: ToolCall[] }) {
  const [aberto, setAberto] = useState<string | null>(null)
  return (
    <div className="overflow-hidden rounded-card bg-sup">
      <table className="w-full text-left text-[13.5px]">
        <thead className="text-[12.5px] text-suave">
          <tr><th className="px-3 py-2 font-medium">Hora</th><th className="px-3 py-2 font-medium">Agente</th><th className="px-3 py-2 font-medium">Ferramenta</th><th className="px-3 py-2 font-medium">Tipo</th><th className="px-3 py-2 text-right font-medium">HTTP</th><th className="px-3 py-2 text-right font-medium">ms</th></tr>
        </thead>
        <tbody>
          {acoes.map((a) => (
            <FragmentLinha key={a.acao_id} a={a} aberto={aberto === a.acao_id} onToggle={() => setAberto(aberto === a.acao_id ? null : a.acao_id)} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FragmentLinha({ a, aberto, onToggle }: { a: ToolCall; aberto: boolean; onToggle: () => void }) {
  const erro = (a.status_http ?? 200) >= 400
  return (
    <>
      <tr className="cursor-pointer border-t border-borda hover:bg-sup2" onClick={onToggle} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onToggle()} aria-expanded={aberto}>
        <td className="num px-3 py-2 text-suave">{hora(a.ts)}</td>
        <td className="px-3 py-2"><span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: corAgente(a.agent) }} />{nomeAgente(a.agent)}</span></td>
        <td className="px-3 py-2"><Mono>{a.ferramenta}</Mono></td>
        <td className={cx('px-3 py-2', a.tipo === 'escrita_critica' && 'font-semibold text-critico')}>{a.tipo.replace('_', ' ')}{a.nivel_autonomia ? ` (${a.nivel_autonomia})` : ''}</td>
        <td className={cx('num px-3 py-2 text-right font-mono', erro ? 'font-semibold text-critico' : 'text-ok')}>{a.status_http ?? '—'}</td>
        <td className="num px-3 py-2 text-right text-suave">{a.duracao_ms ?? '—'}</td>
      </tr>
      {aberto && (
        <tr className="bg-sup2">
          <td colSpan={6} className="px-3 py-3">
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed">{JSON.stringify({ parametros: a.parametros, resultado: a.resultado }, null, 2)}</pre>
          </td>
        </tr>
      )}
    </>
  )
}
