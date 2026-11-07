import { useEffect, useState } from 'react'
import { api } from './api'
import type { Dict } from './types'

// Auditoria real da base: fotos reaproveitadas de outros clientes que foram pagas.
// Vem do insight `fotos_reaproveitadas` do Analista quando existir; senão, os valores auditados.
export interface Auditoria { qtd: number; valor: number }
const RESERVA: Auditoria = { qtd: 9, valor: 527.24 }

export function lerAuditoria(an: Dict | null | undefined): Auditoria {
  const f = an?.fotos_reaproveitadas ?? (an?.clusters ?? []).find((x: Dict) => /foto/.test(String(x.id ?? x.tipo ?? '')))
  const qtd = f?.quantidade ?? f?.total ?? f?.qtd ?? f?.pagas ?? f?.tamanho
  const valor = f?.valor_pago_rs ?? f?.valor_rs ?? f?.custo_rs ?? f?.custo_estimado_rs
  return typeof qtd === 'number' && typeof valor === 'number' ? { qtd, valor } : RESERVA
}

export function useAuditoria(): Auditoria {
  const [a, setA] = useState<Auditoria>(RESERVA)
  useEffect(() => { api.get<Dict>('/api/goxys/analista').then((an) => setA(lerAuditoria(an))).catch(() => {}) }, [])
  return a
}
