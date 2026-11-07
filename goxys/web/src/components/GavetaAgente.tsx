import { useEffect, useRef } from 'react'
import type { Case, Dict, Mascote, TimelineStep } from '../types'
import { useGoxy } from '../store'
import { cx, duracao, hora, NIVEL, nomeCanal } from '../lib'
import { AvatarAgente, corAgente, nomeAgente } from '../mascot'
import { IconeStatus, Mono } from './ui'
import { Miniatura } from '../lightbox'
import { fotoDoSku, useProdutos } from '../produtos'
import { acaoLegivel, AvisoPortal, Evidencias, frase, usouPortal } from './CasoAoVivo'
import type { Aresta } from './Grafo'

export const ORDEM_GRAFO = ['porteiro', 'rastreador', 'detetive', 'analista', 'politica', 'executor', 'mensageiro', 'humano', 'voz']

export const PAPEL: Record<string, string> = {
  porteiro: 'Entende o que o cliente quer', rastreador: 'Junta pedido, entrega e histórico', detetive: 'Confere se a prova é real',
  analista: 'Liga o caso a padrões', politica: 'Decide o nível de autonomia', executor: 'Troca, reenvia ou reembolsa',
  mensageiro: 'Pede aprovação a uma pessoa', humano: 'A pessoa que decide', voz: 'Responde no tom da casa', maestro: 'Coordena o time',
}
const nomeNo = (n: string) => (n === 'politica' ? 'Política' : n === 'humano' ? 'Pessoa' : nomeAgente(n))

const STATUS_PASSO: Record<string, string> = { ok: 'concluído', alerta: 'concluído com alerta', falhou: 'falhou', rodando: 'trabalhando agora', pendente: 'não rodou', pulado: 'não rodou' }

function passosDe(c: Case, n: string): TimelineStep[] {
  if (n === 'politica') return (c.timeline ?? []).filter((s) => s.agent === 'maestro' && /n[íi]vel|revisor|pol[íi]tica/i.test(s.titulo))
  return (c.timeline ?? []).filter((s) => s.agent === n)
}

export interface ResumoNo { nome: string; papel: string; status: string; statusChave: string; frase: string; duracao?: string; numeros: [string, string][] }

/** Resumo curto de um nó para o cartão de hover e o aria-label. */
export function resumoAgente(c: Case, n: string): ResumoNo {
  const ps = passosDe(c, n)
  const ult = ps[ps.length - 1]
  let st = ult?.status ?? 'pendente'
  let f = ult ? frase(ult) : ''
  const numeros: [string, string][] = []
  const res = (ferr: RegExp) => (c.acoes ?? []).filter((a) => a.agent === n && ferr.test(a.ferramenta)).map((a) => a.resultado as Dict).find(Boolean)
  switch (n) {
    case 'detetive': if (c.veracidade?.score != null) numeros.push(['Veracidade', `${Math.round(c.veracidade.score)}/100`]); break
    case 'rastreador': if (c.dossie?.fonte_rastreio) numeros.push(['Fonte', usouPortal(c) ? 'portal' : 'API']); if (c.dossie?.rastreio?.dias_atraso) numeros.push(['Atraso', `${c.dossie.rastreio.dias_atraso} dias`]); break
    case 'executor': { const r = res(/.*/); const p = r?.protocolo ?? r?.solicitacao_id ?? r?.reembolso_id ?? r?.chamado_id; if (p) numeros.push(['Protocolo', String(p)]); break }
    case 'voz': if (c.resposta?.canal || c.canal) numeros.push(['Canal', nomeCanal(c.resposta?.canal ?? c.canal)]); break
    case 'porteiro': if (c.triagem?.intencao) numeros.push(['Intenção', String(c.triagem.intencao).replace(/_/g, ' ')]); break
    case 'politica':
      if (c.decisao?.nivel) { numeros.push(['Nível', c.decisao.nivel]); st = st === 'pendente' ? 'ok' : st; f = f || `decidiu: ${NIVEL[c.decisao.nivel]?.desc.toLowerCase()}` }
      break
    case 'mensageiro': if (c.aprovacao?.status) numeros.push(['Aprovação', c.aprovacao.status]); break
    case 'humano':
      if (c.aprovacao?.status && c.aprovacao.status !== 'pendente') { st = 'ok'; f = `decidiu: ${c.aprovacao.decisao ?? c.aprovacao.status}${c.aprovacao.canal ? ` no ${nomeCanal(c.aprovacao.canal)}` : ''}` }
      else if (c.status === 'aguardando_aprovacao') { st = 'rodando'; f = 'esperando a sua decisão' }
      else if (c.responsavel || c.status === 'humano') { st = 'rodando'; f = c.responsavel ? `${c.responsavel.nome} assumiu` : 'uma pessoa assumiu' }
      break
  }
  const ms = ps.reduce((a, s) => a + (s.duracao_ms ?? 0), 0)
  return { nome: nomeNo(n), papel: PAPEL[n] ?? '', status: STATUS_PASSO[st] ?? st, statusChave: st, frase: f || 'não participou deste caso', duracao: ms ? duracao(ms / 1000) : undefined, numeros: numeros.slice(0, 2) }
}

/** Gaveta larga com a atuação completa de um agente no caso. */
export function GavetaAgente({ c, no, arestas, mascote, onTrocar, onFechar }: { c: Case; no: string; arestas: Aresta[]; mascote: Mascote; onTrocar: (n: string) => void; onFechar: () => void }) {
  const { s } = useGoxy()
  const produtos = useProdutos()
  const ref = useRef<HTMLDivElement>(null)
  const presentes = ORDEM_GRAFO.filter((n) => n === no || passosDe(c, n).length || arestas.some((a) => a.de === n || a.para === n))
  const idx = presentes.indexOf(no)
  const r = resumoAgente(c, no)
  const passos = passosDe(c, no)
  const acoes = (c.acoes ?? []).filter((a) => a.agent === (no === 'politica' ? 'maestro' : no))
  const llm = (s.eventos[c.id] ?? []).filter((e) => e.type === 'llm.call' && e.agent === (no === 'politica' ? 'maestro' : no))
  const tokens = llm.reduce((a, e) => a + (e.data.tokens_in ?? 0) + (e.data.tokens_out ?? 0), 0)
  const usd = llm.reduce((a, e) => a + (e.data.usd ?? 0), 0)
  const saidas = arestas.filter((a) => a.de === no).map((a) => nomeNo(a.para))
  const fotoPedido = fotoDoSku(produtos, c.dossie?.pedido?.sku ?? c.decisao?.sku)
  const fotosCliente = (c.anexos ?? []).filter((a) => a.url).map((a) => ({ src: a.url!, legenda: `Foto enviada pela cliente${a.anexo_id ? ` (${a.anexo_id})` : ''}` }))

  useEffect(() => {
    ref.current?.focus()
    const t = (e: KeyboardEvent) => {
      if (document.querySelector('.lightbox-in')) return // o lightbox está por cima e cuida das teclas
      if (e.key === 'Escape') onFechar()
      else if (e.key === 'ArrowRight' && idx < presentes.length - 1) onTrocar(presentes[idx + 1])
      else if (e.key === 'ArrowLeft' && idx > 0) onTrocar(presentes[idx - 1])
    }
    window.addEventListener('keydown', t)
    return () => window.removeEventListener('keydown', t)
  }, [idx, presentes.join(), onFechar, onTrocar])

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button aria-label="Fechar" className="absolute inset-0 bg-black/50" onClick={onFechar} />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={`O que o ${r.nome} fez`}
        className="gaveta-in rolagem relative h-full w-full max-w-[560px] overflow-y-auto rounded-l-gaveta bg-fundo shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] outline-none">
        <header className="sticky top-0 z-10 flex items-center gap-4 bg-fundo px-7 pb-4 pt-6">
          {no === 'humano' || no === 'politica'
            ? <span className="flex h-[88px] w-[88px] items-center justify-center rounded-full text-[28px] font-bold" style={{ background: `color-mix(in srgb, ${no === 'humano' ? 'var(--espera)' : 'var(--ag-politica)'} 25%, var(--superficie))` }}>{no === 'humano' ? 'P' : c.decisao?.nivel ?? '?'}</span>
            : <AvatarAgente agent={no} mascote={mascote} size={88} ativo={r.statusChave === 'rodando'} />}
          <div className="min-w-0 flex-1">
            <h2 className="text-[24px] font-extrabold" style={{ color: corAgente(no) }}>{r.nome}</h2>
            <p className="text-[14.5px] text-suave">{r.papel}</p>
            <p className="mt-1 inline-flex items-center gap-1.5 text-[14px] font-semibold"><IconeStatus status={r.statusChave} size={14} />{r.status}{r.duracao ? `, ${r.duracao}` : ''}</p>
          </div>
          <div className="flex items-center gap-1 self-start">
            <button onClick={() => idx > 0 && onTrocar(presentes[idx - 1])} disabled={idx <= 0} aria-label="Agente anterior" className="flex h-9 w-9 items-center justify-center rounded-full bg-sup2 disabled:opacity-30">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="m10 3-5 5 5 5" /></svg>
            </button>
            <button onClick={() => idx < presentes.length - 1 && onTrocar(presentes[idx + 1])} disabled={idx >= presentes.length - 1} aria-label="Próximo agente" className="flex h-9 w-9 items-center justify-center rounded-full bg-sup2 disabled:opacity-30">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="m6 3 5 5-5 5" /></svg>
            </button>
            <button onClick={onFechar} aria-label="Fechar" className="ml-1 flex h-9 w-9 items-center justify-center rounded-full bg-sup2">
              <svg width="15" height="15" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m4 4 8 8M12 4l-8 8" /></svg>
            </button>
          </div>
        </header>

        <div className="space-y-6 px-7 pb-12">
          <section>
            <h3 className="text-[16px] font-semibold">O que fez</h3>
            {passos.length === 0 && <p className="mt-1 text-[15px]">{r.frase.charAt(0).toUpperCase() + r.frase.slice(1)}.</p>}
            <ul className="mt-2 space-y-2">
              {passos.map((p, i) => (
                <li key={i} className="rounded-card bg-sup p-3">
                  <p className="text-[15px] font-medium">{frase(p).charAt(0).toUpperCase() + frase(p).slice(1)}</p>
                  {p.detalhe && <p className="mt-1 text-[14px] text-suave">{p.detalhe}</p>}
                  {p.inicio && <p className="mt-1 text-[12.5px] text-suave">{hora(p.inicio)}{p.duracao_ms ? `, ${duracao(p.duracao_ms / 1000)}` : ''}</p>}
                </li>
              ))}
            </ul>
            {no === 'politica' && c.decisao?.nivel && (
              <p className="mt-2 text-[15px]"><b>Nível {c.decisao.nivel}</b>: {acaoLegivel(c.decisao.acao)}. <span className="text-suave">{c.decisao.porque ?? c.decisao.motivo}</span></p>
            )}
            {no === 'rastreador' && usouPortal(c) && <AvisoPortal c={c} />}
          </section>

          {(r.numeros.length > 0) && (
            <section className="flex flex-wrap gap-6">
              {r.numeros.map(([k, v]) => <div key={k}><p className="text-[13px] text-suave">{k}</p><p className="text-[18px] font-bold">{v}</p></div>)}
            </section>
          )}

          {(passos.some((p) => p.evidencias?.length) || (no === 'detetive' && (c.veracidade?.evidencias?.length || fotosCliente.length)) || ((no === 'rastreador' || no === 'detetive') && fotoPedido)) && (
            <section>
              <h3 className="text-[16px] font-semibold">Evidências</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {(no === 'rastreador' || no === 'detetive') && fotoPedido && (
                  <Miniatura src={fotoPedido.img} alt={fotoPedido.nome} legenda="Foto de catálogo, não da cliente" className="h-16 w-16 rounded-[12px] bg-white object-contain ring-1 ring-borda" />
                )}
                {no === 'detetive' && fotosCliente.map((f, i) => <Miniatura key={f.src} src={f.src} legenda={f.legenda} grupo={fotosCliente} indice={i} className="h-16 w-16 rounded-[12px] object-cover" />)}
              </div>
              <div className="mt-2">
                <Evidencias itens={[...passos.flatMap((p) => p.evidencias ?? []), ...(no === 'detetive' ? (c.veracidade?.evidencias ?? []) : [])]} />
              </div>
            </section>
          )}

          {acoes.length > 0 && (
            <section>
              <h3 className="text-[16px] font-semibold">Ferramentas chamadas</h3>
              <ul className="mt-2 space-y-2">
                {acoes.map((a) => {
                  const erro = (a.status_http ?? 200) >= 400
                  return (
                    <li key={a.acao_id} className="rounded-card bg-sup p-3 text-[14px]">
                      <div className="flex flex-wrap items-center gap-2">
                        <Mono className="font-semibold">{a.ferramenta}</Mono>
                        <span className={cx('rounded px-1.5 font-mono text-[12px]', erro ? 'bg-critico-suave text-critico' : 'bg-ok-suave text-ok')}>{a.status_http ?? '—'}</span>
                        {a.duracao_ms != null && <span className="num text-suave">{a.duracao_ms} ms</span>}
                      </div>
                      <p className="mt-1 truncate text-suave">{resumoResultado(a.resultado)}</p>
                      <details className="mt-1">
                        <summary className="cursor-pointer text-[13px] font-semibold text-estrutura">Detalhe técnico</summary>
                        <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-[10px] bg-sup2 p-2 font-mono text-[12px]">{JSON.stringify({ parametros: a.parametros, resultado: a.resultado }, null, 2)}</pre>
                      </details>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          {tokens > 0 && (
            <section>
              <h3 className="text-[16px] font-semibold">Uso de IA</h3>
              <p className="mt-1 text-[15px]"><span className="num font-semibold">{tokens.toLocaleString('pt-BR')}</span> tokens em {llm.length} {llm.length === 1 ? 'chamada' : 'chamadas'}, <span className="num">US$ {usd.toFixed(5)}</span></p>
            </section>
          )}

          {saidas.length > 0 && (
            <section>
              <h3 className="text-[16px] font-semibold">Passou para</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {arestas.filter((a) => a.de === no).map((a) => (
                  <button key={a.para} onClick={() => onTrocar(a.para)} className="rounded-full bg-sup px-3 py-1.5 text-[14px] font-semibold hover:ring-2 hover:ring-estrutura">{nomeNo(a.para)}</button>
                ))}
              </div>
            </section>
          )}
          <p className="text-[12.5px] text-suave">Use ← e → para ir de um agente a outro.</p>
        </div>
      </div>
    </div>
  )
}

function resumoResultado(r: unknown): string {
  if (r == null) return 'sem resultado'
  if (typeof r !== 'object') return String(r)
  const o = r as Dict
  if (o.erro) return `${o.erro}${o.mensagem ? `: ${o.mensagem}` : ''}`
  return Object.entries(o).filter(([, v]) => v != null && typeof v !== 'object').slice(0, 4).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(', ') || 'ok'
}
