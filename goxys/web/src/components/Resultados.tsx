import { useEffect, useMemo, useState } from 'react'
import type { Case, Dict, Mascote, Metricas } from '../types'
import { api } from '../api'
import { brl, cx, duracao } from '../lib'
import { AvatarAgente } from '../mascot'
import { agregarCasos, Grafo } from './Grafo'
import { IconeStatus } from './ui'
import { lerAuditoria } from '../auditoria'
import { ReputacaoRA } from './ReclameAqui'
import { Aprendizado } from './Aprendizado'
import { NumberTicker } from './magic'

const USD_BRL = 5.5

export function Resultados({ cases, mascote }: { cases: Case[]; mascote: Mascote }) {
  const [m, setM] = useState<Metricas | null>(null)
  const [an, setAn] = useState<Dict | null>(null)
  const [placar, setPlacar] = useState<Dict | null>(null)
  useEffect(() => {
    const f = () => api.get<Metricas>('/api/goxys/metricas').then(setM).catch(() => {})
    f()
    api.get<Dict>('/api/goxys/analista').then(setAn).catch(() => {})
    api.get<Dict>('/api/goxys/placar').then(setPlacar).catch(() => {})
    const t = setInterval(f, 5000)
    return () => clearInterval(t)
  }, [])
  const ag = useMemo(() => agregarCasos(cases.filter((c) => c.timeline?.length)), [cases])

  const custoCaso = m && m.total ? ((m.llm?.usd ?? 0) * USD_BRL) / m.total : null
  const tokensCaso = m && m.total ? Math.round((m.llm?.tokens ?? 0) / m.total) : null
  const aud = lerAuditoria(an)
  const sint = placar?.total ? { auto: Math.round(((placar.autonomos ?? 0) / placar.total) * 100), tempo: placar.duracao_s ? placar.duracao_s / placar.total : null, n: placar.total } : null
  const insights: Dict[] = (an?.clusters ?? an?.insights ?? []).slice(0, 4)

  return (
    <div className="rolagem h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-6 pb-16 pt-6">
        <h1 className="text-[30px] font-extrabold tracking-[-0.01em]">Resultados</h1>

        <section className="mt-5 grid gap-6 rounded-col bg-[#2659a5] p-7 text-white lg:grid-cols-[1fr_1.3fr]">
          <div>
            <p className="text-[15px] text-white/85">O que a auditoria da base real encontrou</p>
            <p className="num mt-2 text-[56px] font-extrabold leading-none text-[#d7d900]">{brl(aud.valor)}</p>
            <p className="mt-3 max-w-[34ch] text-[18px] leading-snug">{aud.qtd} fotos reaproveitadas de outros clientes foram pagas. <b>O Goxy teria barrado as {aud.qtd}.</b></p>
          </div>
          <div>
            <div className="flex items-center gap-3">
              <AvatarAgente agent="analista" mascote={mascote} size={44} />
              <p className="text-[17px] font-bold">O Analista sugere</p>
            </div>
            <ul className="mt-3 space-y-3">
              {insights.slice(0, 3).map((x, i) => (
                <li key={x.id ?? i} className="border-l-2 border-[#3dbfef] pl-3">
                  <p className="text-[16px] font-semibold leading-snug">{acaoDoInsight(x)}</p>
                  <p className="text-[13.5px] text-white/85">
                    {x.contatos_evitaveis != null && <>{x.contatos_evitaveis} contatos a menos</>}
                    {x.economia_estimada_rs != null && <>{x.contatos_evitaveis != null ? ', ' : ''}{brl(x.economia_estimada_rs)} poupados</>}
                  </p>
                </li>
              ))}
              {!insights.length && <li className="text-white/85">O Analista ainda não rodou.</li>}
            </ul>
          </div>
        </section>

        <Aprendizado />

        {m && (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Numero valor={duracao(m.tempo_medio_s)} rotulo="tempo médio por caso nesta sessão" ref_={sint?.tempo ? `Nos ${sint.n} clientes sintéticos: ${duracao(sint.tempo)}` : undefined} contra={m.baseline?.tempo_medio_humano_min != null ? `${String(m.baseline.tempo_medio_humano_min).replace('.', ',')} min de uma pessoa` : undefined} cor="var(--marca)" />
            <Numero valor={custoCaso == null ? '—' : custoCaso < 0.01 ? '< R$ 0,01' : brl(custoCaso)} rotulo={`de IA por caso${tokensCaso ? ` (${tokensCaso.toLocaleString('pt-BR')} tokens)` : ''}`} contra={m.baseline?.custo_medio_conversa != null ? `${brl(m.baseline.custo_medio_conversa)} por conversa humana` : undefined} cor="var(--estrutura)" />
            <Numero valor={brl(m.fraude_evitada_rs)} rotulo="em fraude evitada" sub={`${m.fraudes_barradas} ${m.fraudes_barradas === 1 ? 'fraude barrada' : 'fraudes barradas'}`} cor="var(--critico)" />
            <Numero valor={`${Math.round(m.autonomos_pct)}%`} rotulo="resolvido sem pessoa" sub={`de ${m.total} ${m.total === 1 ? 'caso' : 'casos'} nesta sessão`} ref_={sint ? `Nos ${sint.n} clientes sintéticos: ${sint.auto}%` : undefined} cor="var(--ok)" />
          </div>
        )}

        <ReputacaoRA />

        <section className="mt-6 rounded-col bg-sup p-4">
          <h2 className="px-3 pt-2 text-[18px] font-bold text-texto">Como o time trabalhou</h2>
          <p className="px-3 text-[14px] text-suave">O número em cada ligação é quantos casos passaram por ela.</p>
          {ag.arestas.length ? <Grafo mascote={mascote} arestas={ag.arestas} rotulos={ag.rotulos} agregado /> : <p className="px-3 py-10 text-center text-suave">Simule um contato para o grafo ganhar vida.</p>}
        </section>

        <div className="mt-8 max-w-[720px]">
          <Placar p={placar} />
        </div>
      </div>
    </div>
  )
}

function acaoDoInsight(x: Dict): string {
  const n = x.alvo?.notificar_hoje ?? x.alvo?.pedidos_em_transito
  const t = x.chave?.transportadora, uf = x.chave?.uf
  if (n && t) return `Avisar ${n} clientes da ${t}${uf ? ` na ${uf}` : ''}`
  if (x.acao_preventiva) {
    const a = String(x.acao_preventiva).split(/[.;:]/)[0]
    return a.length > 90 ? a.slice(0, 88) + '…' : a
  }
  return x.titulo ?? 'Insight'
}

function Numero({ valor, rotulo, contra, sub, cor, pequeno, ref_ }: { valor: string; rotulo: string; contra?: string; sub?: string; cor: string; pequeno?: boolean; ref_?: string }) {
  return (
    <div className="rounded-col bg-sup p-6">
      <p className={cx('num font-extrabold leading-none tracking-[-0.02em]', pequeno ? 'text-[28px]' : 'text-[38px]')} style={{ color: cor }}><NumberTicker texto={valor} chave={rotulo} /></p>
      <p className="mt-2 text-[15px] font-semibold">{rotulo}</p>
      {contra && <p className="mt-0.5 text-[13.5px] text-suave">contra {contra}</p>}
      {sub && <p className="mt-0.5 text-[13.5px] text-suave">{sub}</p>}
      {ref_ && <p className="mt-2 border-t border-borda pt-2 text-[13px] font-medium text-estrutura">{ref_}</p>}
    </div>
  )
}

const humanizar = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

// História da 1ª rodada (o placar gravado guarda só a última). Se o backend mandar `historico`/`correcoes`, eles valem.
const RODADA_1 = { acertos: 16, total: 20 }
const CORRIGIDOS = ['Reação adversa ao produto', 'Cobrança em dobro', 'Brinde que faltou no pedido', 'Arrependimento da compra']

function Placar({ p }: { p: Dict | null }) {
  if (!p?.total) return <section><h2 className="text-[18px] font-bold text-texto">Clientes sintéticos</h2><p className="mt-2 text-suave">Nenhum placar gravado ainda.</p></section>
  const personas: Dict[] = p.por_persona ?? []
  const erros: Dict[] = p.erros ?? []
  const r1 = p.historico?.[0] ?? RODADA_1
  const corrigidos: string[] = (p.correcoes ?? []).map((x: Dict | string) => (typeof x === 'string' ? x : x.titulo ?? x.persona)).filter(Boolean)
  const lista = corrigidos.length ? corrigidos : CORRIGIDOS
  return (
    <section>
      <h2 className="text-[18px] font-bold text-texto">Clientes sintéticos</h2>
      <p className="mt-1 text-[16px]">
        Primeira rodada <b className="num text-espera-texto">{r1.acertos}/{r1.total}</b> → corrigimos {lista.length} regras → <b className={cx('num', p.acertos === p.total ? 'text-ok' : 'text-espera-texto')}>{p.acertos}/{p.total}</b>
        <span className="text-suave">, {p.autonomos} resolvidos sozinho e {p.escalados} com uma pessoa</span>
      </p>
      <ul className="mt-3 grid gap-1.5 sm:grid-cols-2">
        {lista.map((t) => <li key={t} className="flex items-center gap-2 rounded-[12px] bg-ok-suave px-3 py-2 text-[14px] font-medium text-ok"><IconeStatus status="ok" size={13} />{t}: corrigido</li>)}
      </ul>
      {erros.length > 0 && (
        <div className="mt-3 rounded-card bg-critico-suave p-3 text-[14px]">
          <p className="font-semibold text-critico">{erros.length} {erros.length === 1 ? 'erro nesta rodada' : 'erros nesta rodada'}</p>
          <ul className="mt-1 space-y-0.5">{erros.slice(0, 4).map((e, i) => <li key={i}><b>{humanizar(e.persona ?? '')}</b>: {e.motivo}</li>)}</ul>
        </div>
      )}
      <ul className="mt-4 grid grid-cols-2 gap-1.5 lg:grid-cols-3" aria-label="Personas da última rodada">
        {personas.slice(0, 20).map((x, i) => (
          <li key={i} title={x.descricao ?? ''} className={cx('flex items-center gap-1.5 truncate rounded-full px-3 py-1 text-[13px]', x.ok ? 'bg-ok-suave text-texto' : 'bg-critico-suave text-critico')}>
            <span className={cx('h-2 w-2 shrink-0 rounded-full', x.ok ? 'bg-ok' : 'bg-critico')} />
            <span className="truncate">{humanizar(x.persona ?? '')}</span>
            {x.obtido?.nivel && <span className="ml-auto shrink-0 font-semibold text-suave">{x.obtido.nivel}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
