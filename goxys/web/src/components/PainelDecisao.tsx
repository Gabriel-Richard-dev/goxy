import { useEffect, useState } from 'react'
import type { Aprovacao, Case, Dict, Mascote } from '../types'
import { api, ApiError } from '../api'
import { brl, cx, dataHora, NIVEL } from '../lib'
import { Botao } from './ui'
import { extrairComparacao } from './Comparacao'
import { Iniciais, PassarPara, VOCE } from './Atendimento'
import { acaoLegivel, frase } from './CasoAoVivo'


/** Até 3 frases curtas, dos sinais reais (veracidade.evidencias + decisao.sinais), contando os dois lados. */
export function resumoParaDecidir(c: Case): string[] {
  const par = extrairComparacao(c)
  const sin: Dict = c.decisao?.sinais && typeof c.decisao.sinais === 'object' && !Array.isArray(c.decisao.sinais) ? c.decisao.sinais : {}
  const evs = ((c.veracidade?.evidencias ?? []) as Dict[]).filter((e) => typeof e === 'object' && e?.titulo)
  const tit = (e: Dict) => String(e.titulo).replace(/\s*\(.*?\)/g, '').trim()
  const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
  const contra: string[] = [], favor: string[] = [], contexto: string[] = []

  // contra pagar: fraude objetiva e sinais fortes
  if (par?.mesmoHash) {
    const outra = par.original.customer_id && par.atual.customer_id && par.original.customer_id !== par.atual.customer_id
    contra.push(`Mesma foto já enviada por ${outra ? 'outra cliente' : 'outro pedido'}${par.original.enviado_em ? ` em ${dataHora(par.original.enviado_em).slice(0, 5)}` : ''}`)
  }
  for (const e of evs.filter((e) => (e.peso ?? 0) <= -15 && !(par?.mesmoHash && e.tipo === 'reuso')).sort((a, b) => a.peso - b.peso)) contra.push(cap(tit(e)))
  // prova fraca/ambígua
  const fotoFraca = evs.find((e) => e.tipo === 'foto' && /confian[çc]a\s*([0-5]\d|60)%/i.test(String(e.titulo)))
  const semExif = evs.some((e) => /sem metadados|sem exif|exif ausente/i.test(String(e.titulo)))
  if (fotoFraca || (sin.faixa === 'amarela' && semExif)) contra.push(semExif ? 'Foto pouco clara, sem dados da câmera' : 'Foto não mostra o dano com clareza')
  else if (semExif) contra.push('Foto sem dados da câmera')
  // a favor do cliente
  for (const e of evs.filter((e) => (e.peso ?? 0) > 0)) favor.push(cap(tit(e)))
  const ce: Dict = sin.custo_erro ?? {}
  const limpo = sin.cliente_com_alerta === false && !(sin.risco_cliente ?? []).length
  if (limpo) favor.push(ce.ltv_estimado_rs > 1000 ? `Cliente valioso (LTV ${brl(ce.ltv_estimado_rs)}), sem histórico de fraude` : 'Cliente sem histórico de fraude')
  if (ce.esperado_negar_verdadeira_rs != null && ce.esperado_pagar_indevido_rs != null && !par?.mesmoHash) {
    const negarCaro = ce.esperado_negar_verdadeira_rs > ce.esperado_pagar_indevido_rs
    favor.unshift(negarCaro
      ? `Na média, negar custaria ${brl(ce.esperado_negar_verdadeira_rs)} e pagar indevido, ${brl(ce.esperado_pagar_indevido_rs)}`
      : `Pagar indevido custa mais (${brl(ce.esperado_pagar_indevido_rs)}) que negar (${brl(ce.esperado_negar_verdadeira_rs)})`)
  }
  // contexto
  const r = c.dossie?.rastreio ?? {}
  if (r.atrasado || r.dias_atraso) contexto.push(`Atraso de ${r.dias_atraso ?? '?'} dias${r.transportadora ? ` (${r.transportadora})` : ''}`)
  if (c.triagem?.risco_juridico || sin.risco_juridico) contexto.push('Cliente fala em processo ou Procon')
  if (sin.injecao_no_texto) contexto.push('O texto tenta dar ordens ao Goxy')
  const valor = sin.valor_rs ?? c.decisao?.valor ?? c.dossie?.pedido?.valor
  if (valor) contexto.push(sin.limite_papel_rs && valor > sin.limite_papel_rs ? `Valor ${brl(valor)}, acima do limite do Goxy (${brl(sin.limite_papel_rs)})` : `Valor ${brl(valor)}`)

  const fraude = par?.mesmoHash || sin.fraude_objetiva
  const out = fraude
    ? [...contra.slice(0, 2), ...contexto.slice(-1)]
    : [contra[0], favor[0], contexto[0] ?? favor[1]]
  return out.filter((x): x is string => !!x).filter((x, i, a) => a.indexOf(x) === i).slice(0, 3)
}

const NEGATIVOS = /^(Mesma foto|Foto|Produto da foto n|.*antes da entrega|.*editada|Imagem editada|.*n[aã]o confere|Pagar indevido|O texto|Cliente fala)/i
export const bulletNegativo = (b: string) => NEGATIVOS.test(b) && !/confere com o pedido/i.test(b)

function recomendacao(c: Case): string | null {
  const a = c.decisao?.recomendacao ?? c.decisao?.acao
  if (!a) return c.decisao?.nivel ? 'Decida você: o Goxy não tem certeza' : null
  const m: Record<string, string> = {
    negar: 'Recomendo negar', rejeitar: 'Recomendo negar', reembolso: 'Recomendo reembolsar', reenvio: 'Recomendo reenviar', troca: 'Recomendo trocar',
    chamado_transportadora: 'Recomendo abrir chamado na transportadora', humano_assume: 'Recomendo passar para uma pessoa',
    publicar_resposta_publica_ra: 'Recomendo publicar a resposta', nova_producao: 'Recomendo produzir de novo',
  }
  return m[a] ?? `Recomendo: ${acaoLegivel(a).toLowerCase()}`
}

function resultado(c: Case): string {
  const exec = [...(c.timeline ?? [])].reverse().find((s) => s.agent === 'executor' && ['ok', 'alerta'].includes(s.status))
  switch (c.status) {
    case 'resolvido': {
      const aj = c.aprovacao?.ajuste ?? (c.aprovacao?.decisao === 'ajustar' ? c.decisao?.execucao : null)
      const vf = aj?.valor ?? c.decisao?.execucao?.valor_final
      if ((c.aprovacao?.decisao === 'ajustar' || c.aprovacao?.status === 'ajustada') && vf != null) return `Resolvido: ${acaoLegivel(aj?.acao ?? c.decisao?.acao).toLowerCase()} de ${brl(vf)}, ajustado${c.aprovacao?.aprovador ? ` por ${c.aprovacao.aprovador}` : ''}`
      return `Resolvido: ${exec ? frase(exec) : acaoLegivel(c.decisao?.acao).toLowerCase()}`
    }
    case 'rejeitado': return 'Fraude barrada: reclamação negada'
    case 'humano': return c.responsavel ? `Com ${c.responsavel.nome}` : 'Uma pessoa assumiu o caso'
    case 'aguardando_aprovacao': return 'Esperando uma decisão'
    case 'novo': return 'Na fila, ainda não processado'
    case 'erro': return 'Algo deu errado neste caso'
    default: return 'O time está trabalhando'
  }
}

function mensagemErro(e: unknown): string {
  if (e instanceof ApiError) {
    const b = (e.body ?? {}) as Dict
    if (e.status === 409) return 'Alguém já decidiu este caso em outro canal.'
    if (e.status === 403 && b.erro === 'segregacao') return b.mensagem ?? 'Quem pediu esta ação não pode aprová-la. Peça para outra pessoa decidir.'
    if (e.status === 403 && b.erro === 'alcada') return b.mensagem ?? 'Este valor passa da sua alçada. Passe o caso para um supervisor.'
    if (b.mensagem) return String(b.mensagem)
  }
  return `Não deu para enviar: ${(e as Error).message}`
}

/** Nível B: desfazer dentro da janela, com contagem regressiva. */
function Desfazer({ c }: { c: Case }) {
  const d = c.decisao ?? {}
  const ate: string | undefined = d.desfazer_ate ?? d.desfazer?.ate ?? d.desfazer?.expira_em ?? d.desfazer?.desfazer_ate
  const desfeito = d.desfeito ?? (d.desfazer?.status === 'desfeito' ? d.desfazer : null)
  const [agora, setAgora] = useState(Date.now())
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  useEffect(() => { if (!ate || desfeito) return; const t = setInterval(() => setAgora(Date.now()), 1000); return () => clearInterval(t) }, [ate, desfeito])
  if (d.nivel !== 'B' && !desfeito) return null
  if (desfeito) {
    const nome = typeof desfeito === 'object' ? desfeito.por_nome ?? desfeito.nome ?? desfeito.por : null
    return <p className="mt-3 rounded-card bg-sup2 px-4 py-2.5 text-[14.5px] font-semibold">Desfeito{nome ? ` por ${nome}` : ''}</p>
  }
  if (!ate) return null
  const resta = Math.max(0, Math.floor((new Date(ate).getTime() - agora) / 1000))
  if (!resta) return <p className="mt-3 text-[13.5px] text-suave">O prazo para desfazer acabou.</p>
  const mm = String(Math.floor(resta / 60)).padStart(2, '0'), ss = String(resta % 60).padStart(2, '0')
  return (
    <div className="mt-3">
      <Botao className="w-full" disabled={enviando} onClick={async () => {
        setEnviando(true); setErro(null)
        try { await api.post(`/api/goxys/casos/${c.id}/desfazer`, { por: VOCE, canal: 'painel' }) } catch (e) {
          setErro(e instanceof ApiError && (e.status === 409 || e.status === 410) ? 'O prazo para desfazer acabou.' : mensagemErro(e))
        } finally { setEnviando(false) }
      }}>{enviando ? 'Desfazendo…' : <>Desfazer <span className="num text-suave">{mm}:{ss}</span></>}</Botao>
      {erro && <p role="alert" className="mt-1 text-[13px] font-medium text-critico">{erro}</p>}
    </div>
  )
}

export function PainelDecisao({ c, aprovacao }: { c: Case | null; aprovacao?: Aprovacao; mascote?: Mascote }) {
  const [enviando, setEnviando] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [ajustar, setAjustar] = useState(false)
  const [passar, setPassar] = useState(false)
  const [valor, setValor] = useState('')
  const [nota, setNota] = useState('')
  if (!c) return null

  const bullets = resumoParaDecidir(c)
  const rec = recomendacao(c)
  const nivel = c.decisao?.nivel as string | undefined
  const pendente = aprovacao && aprovacao.status !== 'decidida'
  const publica = aprovacao && (aprovacao.acao_proposta === 'publicar_resposta_publica_ra' || /reclame aqui/i.test(aprovacao.titulo))

  const decidir = async (decisao: 'aprovar' | 'negar' | 'ajustar') => {
    if (!aprovacao) return
    setEnviando(decisao); setErro(null)
    const body: Record<string, unknown> = { decisao, aprovador: 'Supervisor no painel', canal: 'painel' }
    if (decisao === 'ajustar') {
      const v = Number(valor.replace(',', '.'))
      body.ajuste = { ...(valor && !Number.isNaN(v) ? { valor: v } : {}), ...(nota ? { nota } : {}) }
    }
    try { await api.post(`/api/aprovacoes/${aprovacao.aprovacao_id}/decisao`, body); setAjustar(false) } catch (e) {
      setErro(mensagemErro(e))
    } finally { setEnviando(null) }
  }

  return (
    <aside aria-label="Decisão" className="rolagem flex h-full flex-col overflow-y-auto rounded-col bg-sup p-5">
      <h2 className="text-[15px] font-semibold text-texto">{pendente ? 'Resumo para decidir' : 'Resumo'}</h2>
      {bullets.length > 0 && (
        <ul className="mt-3 space-y-2 text-[15px] leading-snug">
          {bullets.map((b) => <li key={b} className="flex gap-2.5"><span className="mt-[8px] h-1.5 w-1.5 shrink-0 rounded-full bg-[#aeb5c0]" />{b}</li>)}
        </ul>
      )}

      {rec && (
        <div className="mt-5 border-t border-borda pt-4">
          <p className="flex items-start gap-2 text-[17px] font-bold leading-snug text-texto">
            <span>{rec}{pendente && aprovacao?.valor ? <> <span className="num">{brl(aprovacao.valor)}</span></> : null}</span>
            {nivel && <span title={`Nível ${nivel}: ${NIVEL[nivel]?.desc}`} className="mt-0.5 inline-flex h-6 shrink-0 items-center rounded-[8px] px-1.5 text-[12px] font-bold text-suave ring-1 ring-borda">{nivel}</span>}
          </p>
        </div>
      )}

      {pendente ? (
        <div className="mt-4 space-y-2">
          {publica && <p className="text-[13.5px] font-semibold text-critico">A resposta fica pública no Reclame Aqui.</p>}
          <Botao variante="marca" className="h-11 w-full" disabled={!!enviando} onClick={() => decidir('aprovar')}>{enviando === 'aprovar' ? 'Aprovando…' : 'Aprovar'}</Botao>
          <Botao variante="perigo" className="h-11 w-full" disabled={!!enviando} onClick={() => decidir('negar')}>{enviando === 'negar' ? 'Negando…' : 'Negar'}</Botao>
          {erro && <p role="alert" className="text-[13px] font-medium text-critico">{erro}</p>}
        </div>
      ) : (
        <p className={cx('mt-4 text-[15px] font-semibold', c.status === 'rejeitado' ? 'text-critico' : 'text-texto')}>{resultado(c)}</p>
      )}

      <div className="relative mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[14.5px]">
        {pendente && <button onClick={() => { setAjustar((x) => !x); setValor(aprovacao?.valor != null ? String(aprovacao.valor) : '') }} aria-expanded={ajustar} className="font-semibold text-estrutura hover:underline">Ajustar</button>}
        <button onClick={() => setPassar((x) => !x)} aria-expanded={passar} className="font-semibold text-estrutura hover:underline">Passar para…</button>
        {passar && <PassarPara c={c} onFechar={() => setPassar(false)} onPassado={() => setPassar(false)} />}
      </div>
      {ajustar && pendente && (
        <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); decidir('ajustar') }}>
          <input inputMode="decimal" value={valor} onChange={(e) => setValor(e.target.value)} aria-label="Novo valor (R$)" placeholder="Novo valor (R$)" className="num w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[15px] text-texto" />
          <input value={nota} onChange={(e) => setNota(e.target.value)} aria-label="Nota para o time" placeholder="Nota para o time" className="w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[14px] text-texto" />
          <Botao type="submit" className="w-full" disabled={!!enviando}>{enviando === 'ajustar' ? 'Enviando…' : 'Enviar ajuste'}</Botao>
        </form>
      )}

      {!pendente && <Desfazer c={c} />}
      {(aprovacao?.escalada_para || c.aprovacao?.escalada_para) && (
        <p className="mt-3 text-[13.5px] font-semibold text-[#a8710a] dark:text-laranja">
          Escalado para {(aprovacao?.escalada_para ?? c.aprovacao?.escalada_para)?.nome ?? 'o supervisor'} (prazo estourado)
        </p>
      )}

      {c.responsavel && (
        <p className="mt-3 flex items-center gap-2 text-[14px]"><Iniciais nome={c.responsavel.nome} size={26} />Com {c.responsavel.nome}</p>
      )}

    </aside>
  )
}
