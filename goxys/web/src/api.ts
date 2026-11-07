import type { BusEvent } from './types'
import { mockRequest, mockStream } from './mock'
import { ApiError } from './errors'

export const MOCK = new URLSearchParams(window.location.search).get('mock') === '1'

export { ApiError }

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (MOCK) return mockRequest(method, path, body) as Promise<T>
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data: unknown = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && typeof data === 'object' && ('mensagem' in data || 'detail' in data))
      ? String((data as any).mensagem ?? (data as any).detail)
      : `HTTP ${res.status}`
    throw new ApiError(res.status, data, msg)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
}

export type StreamStatus = 'conectando' | 'ao_vivo' | 'caiu'

/** SSE com reconexão manual usando since=<último id>. */
export function connectStream(
  getSince: () => number,
  onEvent: (e: BusEvent) => void,
  onStatus: (s: StreamStatus) => void,
): () => void {
  if (MOCK) {
    onStatus('ao_vivo')
    return mockStream(onEvent)
  }
  let es: EventSource | null = null
  let timer: number | undefined
  let fechado = false
  let tentativa = 0

  const abrir = () => {
    if (fechado) return
    onStatus('conectando')
    es = new EventSource(`/api/goxys/stream?since=${getSince()}`)
    es.onopen = () => { tentativa = 0; onStatus('ao_vivo') }
    es.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data)) } catch { /* evento malformado: ignora */ }
    }
    es.onerror = () => {
      es?.close()
      es = null
      onStatus('caiu')
      tentativa++
      timer = window.setTimeout(abrir, Math.min(1000 * tentativa, 5000))
    }
  }
  abrir()
  return () => { fechado = true; es?.close(); window.clearTimeout(timer) }
}
