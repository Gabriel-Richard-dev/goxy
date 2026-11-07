import { useCallback, useEffect, useMemo, useState } from 'react'
import { GoxyProvider, listarAprovacoes, useGoxy } from './store'
import { MOCK } from './api'
import { cx } from './lib'
import { registrarCasos, useCasoExibido } from './exibicao'
import { GoxyComum } from './mascot'
import { Logo } from './components/ui'
import { LightboxProvider } from './lightbox'
import { Fila } from './components/Fila'
import { CasoAoVivo } from './components/CasoAoVivo'
import { PainelDecisao } from './components/PainelDecisao'
import { Board } from './components/Board'
import { Gaveta } from './components/Gaveta'
import { Resultados } from './components/Resultados'
import { Simular } from './components/Simular'
import { Landing } from './components/Landing'

type Tela = 'inicio' | 'ao-vivo' | 'resultados'

// Produção: landing em "/", sistema em "/app/". Dev (Vite só serve /app/): landing em /app/#/inicio.
const SUFIXO = window.location.search
const HREF_INICIO = import.meta.env.DEV ? `/app/${SUFIXO}#/inicio` : `/${SUFIXO}`
const HREF_APP = `/app/${SUFIXO}#/ao-vivo`

function telaAtual(): Tela {
  const h = window.location.hash.replace('#/', '')
  if (!window.location.pathname.startsWith('/app') || h === 'inicio') return 'inicio'
  return h === 'resultados' ? 'resultados' : 'ao-vivo'
}

export default function App() {
  return <GoxyProvider><LightboxProvider><Raiz /></LightboxProvider></GoxyProvider>
}

function Raiz() {
  const { s } = useGoxy()
  const [tela, setTela] = useState<Tela>(telaAtual)
  const [carregando, setCarregando] = useState(tela !== 'inicio')
  useEffect(() => {
    const f = () => setTela(telaAtual())
    window.addEventListener('hashchange', f)
    window.addEventListener('popstate', f)
    return () => { window.removeEventListener('hashchange', f); window.removeEventListener('popstate', f) }
  }, [])
  useEffect(() => { if (carregando) { const t = setTimeout(() => setCarregando(false), 1500); return () => clearTimeout(t) } }, [carregando])
  useTema()

  if (tela === 'inicio') return <Landing mascote={s.mascote} appHref={HREF_APP} />
  if (carregando) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2" role="status">
        <GoxyComum size={180} />
        <p className="text-[16px] font-semibold text-suave">Chamando o time…</p>
      </div>
    )
  }
  return <Painel tela={tela} setTela={setTela} />
}

function Painel({ tela, setTela }: { tela: Tela; setTela: (t: Tela) => void }) {
  const { s } = useGoxy()
  const [sel, setSel] = useState<string | null>(null)
  const [seguindo, setSeguindo] = useState(true)
  const [gaveta, setGaveta] = useState(false)
  const [simular, setSimular] = useState(false)
  const [filaAberta, setFilaAberta] = useState(false)
  const [visao, setVisao] = useState<'caso' | 'board'>(() => { try { return localStorage.getItem('goxy-visao') === 'board' ? 'board' : 'caso' } catch { return 'caso' } })
  const trocarVisao = (v: 'caso' | 'board') => { setVisao(v); try { localStorage.setItem('goxy-visao', v) } catch { /* sem storage */ } }
  const alternador = (
    <div role="tablist" aria-label="Visão" className="inline-flex rounded-full bg-sup p-1 ring-1 ring-borda">
      {(['caso', 'board'] as const).map((v) => (
        <button key={v} role="tab" aria-selected={visao === v} onClick={() => trocarVisao(v)}
          className={cx('rounded-full px-4 py-1 text-[14px] font-semibold', visao === v ? 'bg-azul text-white' : 'text-suave hover:text-texto')}>{v === 'caso' ? 'Caso' : 'Board'}</button>
      ))}
    </div>
  )

  const ir = (t: Tela) => { window.location.hash = `#/${t}`; setTela(t) }
  const cases = useMemo(() => {
    const l = Object.values(s.cases)
    registrarCasos(l) // registra antes de desenhar: base da fila de exibição
    return l.sort((a, b) => b.criado_em.localeCompare(a.criado_em))
  }, [s.cases])
  const aprovacoes = useMemo(() => listarAprovacoes(s), [s])

  useEffect(() => {
    if (!seguindo) return
    const ativo = (s.ultimoCriado ? s.cases[s.ultimoCriado] : undefined) ?? cases.find((c) => c.status === 'em_andamento') ?? cases[0]
    if (ativo && ativo.id !== sel) setSel(ativo.id)
  }, [cases, seguindo, s.ultimoCriado])

  const verCaso = useCallback((id: string) => { setSel(id); setSeguindo(false); setSimular(false); setFilaAberta(false); if (tela !== 'ao-vivo') ir('ao-vivo') }, [tela])
  const real = sel ? s.cases[sel] ?? null : null
  const caso = useCasoExibido(real)
  const pendenteDoCaso = sel ? aprovacoes.find((a) => a.case_id === sel && a.status !== 'decidida') : undefined
  const fecharGaveta = useCallback(() => setGaveta(false), [])
  const fecharSimular = useCallback(() => setSimular(false), [])

  return (
    <div className="sem-respiro flex h-full flex-col">
      <Topo tela={tela} ir={ir} onSimular={() => setSimular(true)} pendentes={tela !== 'ao-vivo' ? aprovacoes.filter((a) => a.status !== 'decidida') : []} onPendente={(id) => id && verCaso(id)} />
      {s.stream === 'caiu' && <p role="status" className="mx-5 mb-2 shrink-0 rounded-full bg-espera-suave px-4 py-1.5 text-center text-[14px] font-semibold text-espera-texto">Reconectando…</p>}
      {tela === 'ao-vivo' && visao === 'board' && (
        <main className="min-h-0 flex-1"><Board cases={cases} aprovacoes={aprovacoes} mascote={s.mascote} alternador={alternador} onAbrir={(id) => { trocarVisao('caso'); verCaso(id) }} /></main>
      )}
      {tela === 'ao-vivo' && visao === 'caso' && (
        <main className="relative grid min-h-0 flex-1 gap-3 px-3 pb-3 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_340px]">
          <section aria-label="Fila de contatos" className={cx('min-h-0 overflow-hidden rounded-col bg-sup', filaAberta ? 'absolute inset-x-3 top-0 z-20 max-h-[70%] shadow-xl lg:static lg:max-h-none lg:shadow-none' : 'hidden lg:block')}>
            <Fila cases={cases} selecionado={sel} mascote={s.mascote} onSelecionar={verCaso} />
          </section>
          <section aria-label="Caso em andamento" className="flex min-h-0 flex-col overflow-hidden">
            {(!seguindo || cases.length > 0) && (
              <div className="flex shrink-0 flex-wrap items-center gap-2 px-5 pt-1">
                <button onClick={() => setFilaAberta((x) => !x)} className="rounded-full bg-sup px-4 py-1.5 text-[14px] font-semibold lg:hidden">Fila ({cases.length})</button>
                <div className="ml-auto flex items-center gap-2">
                  {!seguindo && cases.length > 0 && (
                    <button onClick={() => setSeguindo(true)} title="Seguir o caso mais recente" aria-label="Seguir o caso mais recente"
                      className="flex h-8 w-8 items-center justify-center rounded-full text-suave ring-1 ring-borda hover:text-azul hover:ring-azul">
                      <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden><circle cx="8" cy="8" r="2.2" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" /></svg>
                    </button>
                  )}
                  {alternador}
                </div>
              </div>
            )}
            <div className="min-h-0 flex-1"><CasoAoVivo c={caso} mascote={s.mascote} onDetalhe={() => setGaveta(true)} /></div>
          </section>
          <div className="hidden min-h-0 xl:block"><PainelDecisao c={caso} aprovacao={pendenteDoCaso} mascote={s.mascote} /></div>
          {caso && <div className="xl:hidden lg:col-start-2"><PainelDecisao c={caso} aprovacao={pendenteDoCaso} mascote={s.mascote} /></div>}
        </main>
      )}
      {tela === 'resultados' && <main className="min-h-0 flex-1"><Resultados cases={cases} mascote={s.mascote} /></main>}
      {gaveta && real && <Gaveta c={real} onFechar={fecharGaveta} />}
      {simular && <Simular mascote={s.mascote} cases={s.cases} ultimo={s.ultimoCriado} onFechar={fecharSimular} onVerCaso={verCaso} />}
    </div>
  )
}

function useTema() {
  const [tema] = useState(() => {
    try { const t = localStorage.getItem('goxys-tema'); if (t === 'claro' || t === 'escuro') return t } catch { /* sem storage */ }
    return 'claro'   // padrão: modo claro; o escuro só com escolha explícita do usuário
  })
  useEffect(() => { document.documentElement.dataset.theme = tema === 'escuro' ? 'dark' : 'light' }, [tema])
}

function Topo({ tela, ir, onSimular, pendentes, onPendente }: { tela: Tela; ir: (t: Tela) => void; onSimular: () => void; pendentes: { case_id: string | null }[]; onPendente: (id: string | null) => void }) {
  const [escuro, setEscuro] = useState(() => document.documentElement.dataset.theme === 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = escuro ? 'dark' : 'light'
    try { localStorage.setItem('goxys-tema', escuro ? 'escuro' : 'claro') } catch { /* sem storage */ }
  }, [escuro])

  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3">
      <a href={HREF_INICIO} className="flex items-center gap-2.5" aria-label="Goxy, início">
        <Logo />
      </a>
      <nav className="flex gap-1" aria-label="Telas">
        <a href={HREF_INICIO} className="rounded-full px-4 py-1.5 text-[14.5px] font-semibold text-suave hover:text-texto">Início</a>
        {([['ao-vivo', 'Ao vivo'], ['resultados', 'Resultados']] as const).map(([k, r]) => (
          <button key={k} onClick={() => ir(k)} aria-current={tela === k ? 'page' : undefined}
            className={cx('rounded-full px-4 py-1.5 text-[14.5px] font-semibold transition', tela === k ? 'bg-azul text-white' : 'text-suave hover:text-texto')}>{r}</button>
        ))}
      </nav>
      <div className="ml-auto flex flex-wrap items-center gap-4 text-[13.5px]">
        {MOCK && <span className="rounded-full bg-espera-suave px-2.5 py-0.5 font-semibold text-espera-texto">Modo demonstração</span>}
        {pendentes.length > 0 && (
          <button onClick={() => onPendente(pendentes[0].case_id)} className="inline-flex items-center gap-2 rounded-full bg-espera-suave px-3 py-1.5 text-[14px] font-semibold text-espera-texto hover:brightness-95">
            <span className="h-2 w-2 rounded-full bg-espera" />{pendentes.length === 1 ? '1 aprovação esperando' : `${pendentes.length} aprovações esperando`}
          </button>
        )}
        <button onClick={onSimular} className="rounded-full bg-texto px-4 py-2 text-[14.5px] font-semibold text-fundo hover:opacity-90">Simular contato</button>
        <img src="/assets-goxys/marca/gogroup-logo.png" alt="Gogroup" className="hidden h-5 w-auto min-w-[35px] p-[2px] dark:hidden md:block" />
        <img src="/assets-goxys/marca/gogroup-logo-branco.png" alt="Gogroup" className="hidden h-5 w-auto min-w-[35px] p-[2px] md:dark:block" />
        <button onClick={() => setEscuro((x) => !x)} className="flex h-9 w-9 items-center justify-center rounded-full bg-sup2 hover:bg-borda" aria-label={escuro ? 'Usar modo claro' : 'Usar modo escuro'}>
          {escuro
            ? <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden><circle cx="8" cy="8" r="3" /><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1" /></svg>
            : <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden><path d="M13.5 9.5A6 6 0 0 1 6.5 2.5a6 6 0 1 0 7 7Z" /></svg>}
        </button>
      </div>
    </header>
  )
}
