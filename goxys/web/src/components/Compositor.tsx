import { useEffect, useRef, useState } from 'react'
import type { Dict } from '../types'
import { api } from '../api'
import { brl, cx } from '../lib'
import { fotoDoSku, useProdutos } from '../produtos'
import { IconeCanal } from './ui'
import { Ancorado } from './Ancorado'

// Compositor de contato livre: o usuário escreve como o cliente, opcionalmente escolhendo um pedido real.
interface Pedido { order_id: string; customer_id?: string; cliente_nome?: string; produto?: string; sku?: string; valor?: number; status_entrega?: string; data_pedido?: string }

const CANAIS: [string, string][] = [['whatsapp', 'WhatsApp'], ['email', 'E-mail'], ['reclame_aqui', 'Reclame Aqui'], ['telegram', 'Telegram']]
const SUGESTOES: [string, (p: Pedido) => string][] = [
  ['chegou quebrado', (p) => `Oi! Meu pedido ${p.order_id} chegou quebrado. Mandei a foto.`],
  ['não chegou ainda', (p) => `Meu pedido ${p.order_id} ainda não chegou e o prazo já passou. Cadê?`],
  ['veio errado', (p) => `O pedido ${p.order_id} veio errado, não é o produto que eu comprei.`],
  ['quero cancelar', (p) => `Quero cancelar o pedido ${p.order_id}, por favor.`],
]
const primeiroNome = (n?: string) => (n ?? 'Cliente').split(' ')[0]

export function Compositor({ onEnviado }: { onEnviado: (caseId: string | null) => void }) {
  const [canal, setCanal] = useState('whatsapp')
  const [texto, setTexto] = useState('')
  const [pedido, setPedido] = useState<Pedido | null>(null)
  const [foto, setFoto] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const ta = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { ta.current?.focus() }, [])

  const enviar = async () => {
    if (!texto.trim()) return
    setEnviando(true); setErro(null)
    let msg = texto.trim()
    if (pedido && !msg.includes(pedido.order_id)) msg += ` (pedido ${pedido.order_id})`
    try {
      const r = await api.post<Dict>('/api/goxys/casos', {
        canal, mensagem: msg,
        ...(pedido ? { order_id: pedido.order_id, customer_id: pedido.customer_id } : {}),
        ...(foto?.trim() ? { anexos: [{ tipo: 'foto_produto', descricao_visual: foto.trim(), anexo_id: `SIM-${Date.now()}` }] } : {}),
      })
      setTexto(''); setFoto(null)
      onEnviado(r?.case_id ?? null)
    } catch (e) { setErro(`Não deu para enviar: ${(e as Error).message}`) } finally { setEnviando(false) }
  }

  return (
    <form className="rounded-col bg-sup p-5" onSubmit={(e) => { e.preventDefault(); enviar() }}>
      <h3 className="text-[17px] font-bold">Escreva como o cliente</h3>
      <div role="radiogroup" aria-label="Canal" className="mt-3 inline-flex flex-wrap gap-1 rounded-full bg-sup2 p-1">
        {CANAIS.map(([k, r]) => (
          <button key={k} type="button" role="radio" aria-checked={canal === k} onClick={() => setCanal(k)}
            className={cx('inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13.5px] font-semibold', canal === k ? 'bg-azul text-white' : 'text-suave hover:text-texto')}>
            <IconeCanal canal={k} size={14} />{r}
          </button>
        ))}
      </div>

      <div className="mt-3"><SeletorPedido pedido={pedido} onEscolher={setPedido} /></div>

      {pedido && (
        <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Sugestões de mensagem">
          {SUGESTOES.map(([r, f]) => (
            <button key={r} type="button" onClick={() => { setTexto(f(pedido)); ta.current?.focus() }}
              className="rounded-full px-3 py-1 text-[13px] font-semibold text-azul ring-1 ring-borda hover:ring-azul">{r}</button>
          ))}
        </div>
      )}

      <label className="mt-3 block">
        <span className="sr-only">Mensagem do cliente</span>
        <textarea ref={ta} rows={3} value={texto} onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); enviar() } }}
          placeholder={pedido ? `Mensagem de ${primeiroNome(pedido.cliente_nome)} sobre o pedido ${pedido.order_id}…` : 'Escreva como o cliente… (sem pedido, o Goxy vai pedir o número)'}
          className="w-full resize-y rounded-[14px] border border-borda bg-fundo px-4 py-3 text-[16px] leading-relaxed text-texto placeholder:text-suave" />
      </label>

      {foto === null ? (
        <button type="button" onClick={() => setFoto('')} className="mt-2 text-[13.5px] font-semibold text-estrutura hover:underline">Anexar foto (simulada)</button>
      ) : (
        <div className="mt-2 rounded-[14px] bg-sup2 p-3">
          <div className="flex items-center gap-2 text-[13px]">
            <span className="rounded-full bg-sup px-2 py-0.5 font-semibold text-suave ring-1 ring-borda">foto simulada</span>
            <span className="text-suave">descreva o que a foto mostraria; vai como descrição visual, sem hash</span>
            <button type="button" onClick={() => setFoto(null)} className="ml-auto font-semibold text-estrutura hover:underline">Tirar</button>
          </div>
          <input value={foto} onChange={(e) => setFoto(e.target.value)} placeholder="Ex.: copo com a tampa rachada na borda" aria-label="Descrição da foto simulada"
            className="mt-2 w-full rounded-[12px] border border-borda bg-fundo px-3 py-2 text-[14px] text-texto" />
        </div>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button type="submit" disabled={!texto.trim() || enviando} className="h-11 rounded-full bg-azul px-6 text-[15px] font-semibold text-white disabled:opacity-40">
          {enviando ? 'Enviando…' : 'Enviar como cliente'}
        </button>
        <span className="text-[12.5px] text-suave">Ctrl + Enter envia</span>
      </div>
      {erro && <p role="alert" className="mt-2 text-[13px] font-medium text-critico">{erro}</p>}
    </form>
  )
}

function SeletorPedido({ pedido, onEscolher }: { pedido: Pedido | null; onEscolher: (p: Pedido | null) => void }) {
  const [q, setQ] = useState('')
  const [aberto, setAberto] = useState(false)
  const [lista, setLista] = useState<Pedido[]>([])
  const [carregando, setCarregando] = useState(false)
  const [cursor, setCursor] = useState(0)
  const caixa = useRef<HTMLDivElement>(null)
  const campo = useRef<HTMLInputElement>(null)
  const produtos = useProdutos()

  // busca com debounce de 250 ms
  useEffect(() => {
    if (!aberto) return
    setCarregando(true)
    const t = setTimeout(() => {
      api.get<Pedido[]>(`/api/erp/pedidos?q=${encodeURIComponent(q.trim())}`)
        .then((l) => { setLista(Array.isArray(l) ? l.slice(0, 30) : []); setCursor(0) })
        .catch(() => setLista([]))
        .finally(() => setCarregando(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q, aberto])

  useEffect(() => {
    if (!aberto) return
    const fora = (e: MouseEvent) => { const t = e.target as Node; if (!caixa.current?.contains(t) && !campo.current?.contains(t)) setAberto(false) }
    document.addEventListener('mousedown', fora)
    return () => document.removeEventListener('mousedown', fora)
  }, [aberto])
  useEffect(() => { caixa.current?.querySelector('[data-cursor="1"]')?.scrollIntoView({ block: 'nearest' }) }, [cursor])

  const escolher = (p: Pedido) => { onEscolher(p); setAberto(false); setQ('') }
  const tecla = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setAberto(true); setCursor((x) => Math.min(lista.length - 1, x + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((x) => Math.max(0, x - 1)) }
    else if (e.key === 'Enter' && aberto && lista[cursor]) { e.preventDefault(); escolher(lista[cursor]) }
    else if (e.key === 'Escape' && aberto) { e.preventDefault(); e.stopPropagation(); setAberto(false) }
  }

  if (pedido) {
    const f = fotoDoSku(produtos, pedido.sku)
    return (
      <div className="flex items-center gap-3 rounded-[14px] border border-borda bg-fundo px-3 py-2">
        {f ? <img src={f.img} alt="" className="h-10 w-10 rounded-[10px] bg-white object-contain" /> : <span className="h-10 w-10 rounded-[10px] bg-sup2" />}
        <div className="min-w-0 flex-1 text-[14px]">
          <p className="truncate font-semibold">{pedido.produto ?? 'Pedido'}</p>
          <p className="truncate text-[12.5px] text-suave">{primeiroNome(pedido.cliente_nome)} · <span className="font-mono">{pedido.order_id}</span>{pedido.valor != null ? ` · ${brl(pedido.valor)}` : ''}</p>
        </div>
        <button type="button" onClick={() => onEscolher(null)} className="text-[13px] font-semibold text-estrutura hover:underline">Trocar</button>
      </div>
    )
  }

  return (
    <div className="relative">
      <input ref={campo} role="combobox" aria-expanded={aberto} aria-controls="lista-pedidos" aria-autocomplete="list"
        value={q} onChange={(e) => { setQ(e.target.value); setAberto(true) }} onFocus={() => setAberto(true)} onKeyDown={tecla}
        placeholder="Pedido do cliente (opcional): busque por produto, cliente ou número"
        className="w-full rounded-[14px] border border-borda bg-fundo px-4 py-2.5 text-[14.5px] text-texto placeholder:text-suave" />
      {aberto && (
        <Ancorado largura="ancora" minLargura={360} alinhar="esquerda" altura={360}>
          {(max) => (
            <div ref={caixa} id="lista-pedidos" role="listbox" aria-label="Pedidos"
              className="rolagem overflow-y-auto rounded-[18px] bg-sup p-1.5 shadow-[0_10px_30px_-14px_rgba(0,0,0,.3)] ring-1 ring-borda" style={{ maxHeight: Math.max(200, Math.min(max, 380)) }}>
              {carregando && !lista.length && <p className="px-3 py-3 text-[13.5px] text-suave">Buscando pedidos…</p>}
              {!carregando && !lista.length && <p className="px-3 py-3 text-[13.5px] text-suave">Nenhum pedido encontrado.</p>}
              {lista.map((p, i) => {
                const f = fotoDoSku(produtos, p.sku)
                const ativo = i === cursor
                return (
                  <div key={p.order_id} role="option" aria-selected={ativo} data-cursor={ativo ? '1' : undefined}
                    onMouseEnter={() => setCursor(i)} onMouseDown={(e) => { e.preventDefault(); escolher(p) }}
                    className={cx('flex cursor-pointer items-center gap-3 rounded-[12px] px-2.5 py-2', ativo && 'bg-marca-suave')}>
                    {f ? <img src={f.img} alt="" className="h-10 w-10 shrink-0 rounded-[10px] bg-white object-contain ring-1 ring-borda" /> : <span className="h-10 w-10 shrink-0 rounded-[10px] bg-sup2" />}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-semibold">{p.produto ?? 'Produto'}</p>
                      <p className="truncate text-[12.5px] text-suave">{primeiroNome(p.cliente_nome)} · <span className="font-mono">{p.order_id}</span>{p.status_entrega ? ` · ${p.status_entrega.replace(/_/g, ' ')}` : ''}</p>
                    </div>
                    {p.valor != null && <span className="num shrink-0 text-[13px] font-semibold">{brl(p.valor)}</span>}
                  </div>
                )
              })}
            </div>
          )}
        </Ancorado>
      )}
    </div>
  )
}
