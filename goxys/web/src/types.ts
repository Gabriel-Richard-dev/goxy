// Espelho de goxys/core/state.py e CONTRACTS.md §5/§6.

export type CaseStatus =
  | 'novo' | 'em_andamento' | 'aguardando_aprovacao' | 'resolvido' | 'rejeitado' | 'humano' | 'erro'
export type StepStatus = 'pendente' | 'rodando' | 'ok' | 'alerta' | 'falhou' | 'pulado'
export type MascotState = 'parado' | 'investigando' | 'fraude' | 'esperando' | 'resolvido' | 'humano'

export const AGENTES = ['maestro', 'porteiro', 'rastreador', 'detetive', 'executor', 'voz', 'mensageiro', 'analista'] as const
export type Agente = (typeof AGENTES)[number]

export interface Anexo {
  anexo_id?: string | null
  tipo?: string | null
  hash_perceptual?: string | null
  exif_data_captura?: string | null
  exif_modelo_camera?: string | null
  exif_software?: string | null
  descricao_visual?: string | null
  url?: string | null
  enviado_em?: string | null
  order_id?: string | null
  customer_id?: string | null
}

export interface TimelineStep {
  agent: string
  status: StepStatus
  titulo: string
  detalhe: string
  evidencias: Record<string, unknown>[]
  inicio?: string | null
  fim?: string | null
  duracao_ms?: number | null
}

export interface ToolCall {
  acao_id: string
  agent: string
  ferramenta: string
  tipo: 'leitura' | 'escrita' | 'escrita_critica' | 'humano' | 'llm'
  parametros: Record<string, unknown>
  resultado?: unknown
  status_http?: number | null
  nivel_autonomia?: string | null
  ts: string
  duracao_ms?: number | null
}

export type Dict = Record<string, any>

export interface Case {
  id: string
  canal: string
  customer_id?: string | null
  order_id?: string | null
  conversation_id?: string | null
  demo_caso?: number | null
  cliente_nome?: string | null
  mensagem: string
  mensagens: { autor: string; nome?: string; texto: string; ts?: string }[]
  responsavel?: Pessoa | null
  sugestao_responsavel?: (Pessoa & { motivo?: string }) | null
  resumo_passagem?: string | string[] | null
  anexos: Anexo[]
  telegram_chat_id?: string | null
  status: CaseStatus
  mascot_state: MascotState
  criado_em: string
  atualizado_em: string
  encerrado_em?: string | null
  plano: Dict[]
  triagem: Dict
  dossie: Dict
  veracidade: Dict
  decisao: Dict
  aprovacao: Dict
  resposta: Dict
  acoes: ToolCall[]
  timeline: TimelineStep[]
  llm_uso: { chamadas: number; tokens_in: number; tokens_out: number; usd: number }
}

export interface BusEvent {
  id: number
  type: string
  ts: string
  case_id: string | null
  agent: string | null
  data: Dict
}

export interface Aprovacao {
  aprovacao_id: string
  case_id: string | null
  titulo: string
  resumo: string
  nivel?: string
  valor?: number | null
  acao_proposta?: string
  evidencias: unknown[]
  expira_em?: string | null
  status: 'pendente' | 'decidida' | 'escalada'
  decisao?: string
  aprovador?: string
  canal?: string
  ajuste?: Dict | null
  tempo_resposta_s?: number | null
  escalada_para?: { colaborador_id?: string; nome?: string } | null
  pedido_em: string
}

export interface Metricas {
  total: number
  autonomos_pct: number
  tempo_medio_s: number
  fraude_evitada_rs: number
  fraudes_barradas: number
  aprovacoes_pendentes: number
  tempo_medio_aprovacao_s: number
  contatos_evitados: number
  por_nivel: Record<string, number>
  por_status: Record<string, number>
  llm: { chamadas: number; tokens: number; usd: number }
  baseline: { tempo_medio_humano_min: number; custo_medio_conversa: number }
}

export interface Saude { ia: boolean; telegram: boolean; slack: boolean; db: boolean }
export interface MidiaMascote { video?: string; img?: string; img_sm?: string; poster?: string }
export interface Mascote { agentes: Record<string, MidiaMascote>; estados: Record<string, MidiaMascote>; base?: string; base_sm?: string; hero?: MidiaMascote }

export interface Pessoa { colaborador_id: string; nome: string; cargo: string; desde?: string; nota?: string | null }
export interface Membro extends Pessoa { nivel?: string; canais_atendidos?: string | string[]; supervisor_id?: string | null; status?: string }
