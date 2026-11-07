import type { Anexo, Case, Dict } from '../types'
import { cx, dataHora, nomeCanal } from '../lib'
import { Mono } from './ui'
import { fotoDoSku, useProdutos } from '../produtos'
import { Miniatura } from '../lightbox'

// O dataset não tem os arquivos de imagem: cada anexo traz hash, EXIF e a descrição do módulo de visão.
// A perícia mostra esses dados reais em "cartões de evidência", nunca uma foto desenhada.

export type AnexoEv = Anexo & { canal?: string | null; produto?: string | null; conversation_id?: string | null }
export interface ParFotos { original: AnexoEv; atual: AnexoEv; similaridade?: number; diferencas: string[]; indicios: string[]; outros: Dict[]; mesmoHash: boolean }

export function extrairComparacao(c: Case): ParFotos | null {
  const v = c.veracidade?.comparacao as Dict | Dict[] | undefined
  if (!v) return null
  let original: Dict | undefined
  let atual: Dict | undefined
  let extra: Dict = {}
  if (Array.isArray(v)) {
    ;[original, atual] = v
  } else {
    extra = v
    original = v.original ?? v.anterior ?? v.antiga ?? v.foto_original ?? v.referencia
    atual = v.atual ?? v.nova ?? v.recebida ?? v.foto_atual ?? v.enviada
  }
  if (!atual) atual = c.anexos?.[0]
  if (!original || !atual) return null
  const h = extra.hash_perceptual ?? original.hash_perceptual ?? atual.hash_perceptual
  original = { ...original, hash_perceptual: original.hash_perceptual ?? h }
  atual = {
    ...atual,
    hash_perceptual: atual.hash_perceptual ?? (extra.mesmo_hash ? h : undefined) ?? c.anexos?.[0]?.hash_perceptual,
    order_id: atual.order_id ?? c.order_id, customer_id: atual.customer_id ?? c.customer_id,
    canal: atual.canal ?? c.canal, produto: atual.produto ?? produtoDoPedido(c),
  }
  const sim = extra.similaridade ?? extra.similarity ?? extra.semelhanca
  return {
    original, atual,
    similaridade: typeof sim === 'number' ? (sim <= 1 ? sim * 100 : sim) : undefined,
    diferencas: toArr(extra.diferencas),
    indicios: toArr(extra.indicios_ia ?? c.veracidade?.indicios_ia),
    outros: Array.isArray(extra.outros_usos) ? extra.outros_usos : [],
    mesmoHash: !!extra.mesmo_hash || (!!original.hash_perceptual && original.hash_perceptual === atual.hash_perceptual),
  }
}
const toArr = (x: unknown): string[] => (Array.isArray(x) ? x.map(String) : x ? [String(x)] : [])

function produtoDoPedido(c: Case): string | undefined {
  const p = c.dossie?.pedido ?? {}
  return p.descricao_produto ?? p.produto ?? p.nome_produto ?? p.itens?.[0]?.nome ?? p.itens?.[0]?.produto ?? undefined
}

/** CH585887 -> CH58••87 */
export const mascararCliente = (id?: string | null) => (!id ? '—' : id.length <= 6 ? id : `${id.slice(0, 4)}••${id.slice(-2)}`)

const dataEntrega = (c: Case): string | undefined => {
  const d = c.dossie ?? {}
  return d.rastreio?.entregue_em ?? d.rastreio?.data_entrega ?? d.pedido?.data_entrega ?? d.pedido?.entregue_em ?? undefined
}

/** Divergências do anexo recebido, em vermelho: EXIF antes da entrega, produto que não confere, software suspeito. */
function divergencias(c: Case, a: AnexoEv): string[] {
  const out: string[] = []
  const ent = dataEntrega(c)
  if (a.exif_data_captura && ent && a.exif_data_captura.slice(0, 10) < ent.slice(0, 10)) out.push(`Foto tirada em ${dataHora(a.exif_data_captura)}, antes da entrega (${dataHora(ent).slice(0, 5)})`)
  for (const e of (c.veracidade?.evidencias ?? []) as Dict[]) {
    if (typeof e !== 'object' || !e.titulo) continue
    if (/n[aã]o confere/i.test(e.titulo)) out.push(`${e.titulo}${e.detalhe ? `: ${e.detalhe}` : ''}`)
    else if (/antes da entrega/i.test(e.titulo) && !out.some((x) => /antes da entrega/.test(x))) out.push(`${e.titulo}${e.detalhe ? ` (${e.detalhe.replace(/ · /g, ', ')})` : ''}`)
  }
  if (a.exif_software && /photoshop|firefly|midjourney|dall|stable|gimp|canva|gerad|generativ/i.test(a.exif_software)) out.push(`Editada em ${a.exif_software}`)
  return out
}

/** A perícia do Detetive: dois anexos reais lado a lado. */
export function Comparacao({ c, par, resumida, onVerMais }: { c: Case; par: ParFotos; compacta?: boolean; resumida?: boolean; onVerMais?: () => void }) {
  const { original, atual, mesmoHash } = par
  const score = c.veracidade?.score as number | undefined
  const div = divergencias(c, atual)
  const outroCliente = original.customer_id && atual.customer_id && original.customer_id !== atual.customer_id
  const produtos = useProdutos()
  const fotoPedido = fotoDoSku(produtos, c.dossie?.pedido?.sku ?? c.decisao?.sku)

  return (
    <section aria-label="Perícia da foto" className="relative overflow-hidden rounded-col bg-[#2659a5] p-5 text-white">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="text-[19px] font-semibold leading-tight">{mesmoHash ? `A mesma foto, enviada antes por ${outroCliente ? 'outra pessoa' : 'outro pedido'}` : 'Foto parecida com uma já enviada'}</h3>
          <p className="mt-1 max-w-[60ch] text-[14px] text-white/85">O que a plataforma de visão registrou em cada anexo, lado a lado.</p>
        </div>
        {score != null && (
          <div className="flex items-baseline gap-1 rounded-card bg-white text-[#333333] px-4 py-2">
            <span className="num text-[44px] font-extrabold leading-none text-[#e5273c]">{Math.round(score)}</span>
            <span className="text-[13px] text-[#666b75]">/100<br />veracidade</span>
          </div>
        )}
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <CartaoEvidencia a={original} rotulo="Enviada antes" mesmoHash={mesmoHash} />
        <CartaoEvidencia a={atual} rotulo="Recebida neste caso" mesmoHash={mesmoHash} carimbo={mesmoHash} divergencias={div} />
      </div>

      {fotoPedido && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <figure className="flex items-center gap-4 rounded-card bg-white p-3 text-[#333333]">
            <Miniatura src={fotoPedido.img} alt={fotoPedido.nome} legenda="Foto de catálogo, não da cliente" className="h-16 w-16 rounded-[12px] bg-[#f5f5f5] object-contain" />
            <figcaption className="min-w-0">
              <p className="text-[13px] font-semibold text-[#666b75]">O que a cliente comprou</p>
              <p className="text-[15px] font-semibold leading-snug">{atual.produto ?? fotoPedido.nome}</p>
              <p className="mt-0.5 text-[12px] text-[#666b75]">foto de catálogo, não da cliente</p>
            </figcaption>
          </figure>
          <div className="rounded-card bg-white text-[#333333] p-4 ring-1 ring-[#e5273c]">
            <p className="text-[13px] font-semibold text-[#666b75]">O que a foto enviada mostra</p>
            <p className="mt-1 text-[15px] leading-snug">"{atual.descricao_visual || 'sem descrição'}"</p>
          </div>
        </div>
      )}

      {par.outros.length > 0 && (
        <div className="mt-4 rounded-card bg-[#e61782]/15 px-4 py-3">
          <p className="text-[15.5px] font-semibold">A mesma foto já apareceu em {par.outros.length === 1 ? '1 outro pedido' : `${par.outros.length} outros pedidos`}</p>
          <ul className="mt-1 space-y-0.5 text-[13.5px] text-white/85">
            {par.outros.map((o, i) => (
              <li key={o.anexo_id ?? i}>
                <Mono className="text-white">{o.order_id ?? '—'}</Mono>, cliente <Mono className="text-white">{mascararCliente(o.customer_id)}</Mono>
                {o.enviado_em ? `, em ${dataHora(o.enviado_em)}` : ''}{o.anexo_id ? <>, anexo <Mono className="text-white">{o.anexo_id}</Mono></> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      {resumida ? (
        onVerMais && (
          <div className="mt-4 flex justify-end">
            <button onClick={onVerMais} className="rounded-full bg-white px-4 py-2 text-[14px] font-semibold text-[#333333] hover:bg-[#eef1f6]">Ver a perícia completa</button>
          </div>
        )
      ) : (par.diferencas.length > 0 || par.indicios.length > 0) && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {par.diferencas.length > 0 && <Lista titulo="O que mudou" itens={par.diferencas} cor="#d7d900" />}
          {par.indicios.length > 0 && <Lista titulo="Sinais de edição" itens={par.indicios} cor="#e61782" />}
        </div>
      )}
    </section>
  )
}

function Lista({ titulo, itens, cor }: { titulo: string; itens: string[]; cor: string }) {
  return (
    <div>
      <h4 className="text-[13.5px] font-semibold text-white/85">{titulo}</h4>
      <ul className="mt-1 space-y-1 text-[14px]">{itens.map((d) => <li key={d} className="flex gap-2"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: cor }} />{d}</li>)}</ul>
    </div>
  )
}

/** Cartão de evidência de um anexo real (também usado na landing). */
export function CartaoEvidencia({ a, rotulo, mesmoHash, carimbo, divergencias = [], carimboVisivel = true }: {
  a: AnexoEv; rotulo: string; mesmoHash?: boolean; carimbo?: boolean; divergencias?: string[]; carimboVisivel?: boolean
}) {
  return (
    <article className="relative flex min-w-0 flex-col rounded-card bg-white p-4 text-[#333333]">
      {carimbo && carimboVisivel && (
        <span className="carimbo absolute -right-1 -top-2 z-10 rounded-[10px] border-[3px] border-[#e61782] bg-white px-2 py-0.5 text-[14px] font-extrabold text-[#e61782]">Mesma foto</span>
      )}
      <p className="text-[13px] font-semibold text-[#666b75]">{rotulo}</p>
      <blockquote className="mt-1 text-[15.5px] font-medium leading-snug">
        "{a.descricao_visual || 'sem descrição da plataforma'}"
        <span className="mt-0.5 block text-[12px] font-normal text-[#666b75]">o que a plataforma viu na foto</span>
      </blockquote>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13.5px]">
        <dt className="text-[#666b75]">Anexo</dt><dd><Mono>{a.anexo_id ?? '—'}</Mono>{a.canal ? `, ${nomeCanal(a.canal)}` : ''}{a.enviado_em ? `, ${dataHora(a.enviado_em)}` : ''}</dd>
        <dt className="text-[#666b75]">Cliente</dt><dd><Mono>{mascararCliente(a.customer_id)}</Mono></dd>
        <dt className="text-[#666b75]">Pedido</dt><dd><Mono>{a.order_id ?? '—'}</Mono>{a.produto ? `, ${a.produto}` : ''}</dd>
        <dt className="text-[#666b75]">Captura</dt><dd>{a.exif_data_captura ? dataHora(a.exif_data_captura) : <span className="font-semibold text-[#e5273c]">sem EXIF</span>}{a.exif_modelo_camera ? `, ${a.exif_modelo_camera}` : ''}</dd>
        <dt className="text-[#666b75]">Hash</dt>
        <dd><Mono className={cx('rounded px-1.5 py-0.5', mesmoHash ? 'bg-[#e61782] font-semibold text-white' : 'bg-[#eef1f6]')}>{a.hash_perceptual ?? '—'}</Mono></dd>
      </dl>
      {divergencias.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-[#eef1f6] pt-2 text-[13.5px] font-medium text-[#e5273c]">
          {divergencias.map((d) => <li key={d} className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-[#e5273c]" />{d}</li>)}
        </ul>
      )}
    </article>
  )
}
