// 类型定义 - 避雷购物助手

export interface EvidenceItem {
  evidence_id: string
  product_name: string
  evidence_type: 'review' | 'price' | 'risk'
  source_url?: string
  source_title: string
  platform: string
  author?: string
  snippet: string
  claims: string[]
  search_query?: string
  retrieved_at: string
}

export interface ReviewSource {
  platform: string
  author: string
  title: string
  url?: string
  stance: string
  is_sponsored: boolean
  key_points: string[]
  credibility_score: number
  evidence_ids: string[]
}

export interface Product {
  name: string
  brand: string
  model: string
  price_range: string
  rating?: number
  image_url?: string
  specs?: Record<string, any>
  price_evidence_ids: string[]
  spec_evidence_ids: string[]
}

export interface ProductAnalysis {
  product: Product
  reviews: ReviewSource[]
  common_pros: string[]
  common_cons: string[]
  red_flags: string[]
  controversy_points: string[]
  verdict: string
  verdict_reason: string
  pro_evidence_ids: Record<string, string[]>
  con_evidence_ids: Record<string, string[]>
  red_flag_evidence_ids: Record<string, string[]>
  controversy_evidence_ids: Record<string, string[]>
  verdict_evidence_ids: string[]
}

export interface ShoppingReport {
  query: string
  category: string
  products: ProductAnalysis[]
  comparison_summary: string
  final_recommendation: string
  budget_advice?: string
  general_tips: string[]
  comparison_evidence_ids: string[]
  recommendation_evidence_ids: string[]
  budget_evidence_ids: string[]
  evidence: EvidenceItem[]
  citation_warnings: string[]
}

export interface ShoppingFormData {
  product_name: string
  budget_min?: number
  budget_max?: number
  brand_preferences: string[]
  usage_scenario: string
  concerns: string[]
  free_text_input: string
}

export interface ShoppingReportResponse {
  success: boolean
  message: string
  data?: ShoppingReport
}

export interface TaskTraceEvent {
  event_id: string
  step_key: string
  step_name: string
  status: 'running' | 'success' | 'failed' | 'partial' | string
  message: string
  started_at: string
  ended_at?: string
  duration_ms?: number
  attempt_count: number
  tool_call_count: number
  attempts: StepAttemptTrace[]
  error_type?: string
  error_message?: string
}

export interface SearchCallTrace {
  query: string
  status: 'pending' | 'running' | 'success' | 'empty' | 'failed' | 'cancelled' | string
  duration_ms?: number
  result_chars: number
  error_type?: string
  error_message?: string
}

export interface StepAttemptTrace {
  attempt: number
  status: 'success' | 'failed' | string
  duration_ms: number
  tool_call_count: number
  model_duration_ms?: number
  search_calls: SearchCallTrace[]
  error_type?: string
  error_message?: string
}

export interface ShoppingAnalysisTaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'succeeded' | 'partial' | 'failed' | string
  current_step?: string
  progress: number
  message: string
  created_at: string
  updated_at: string
  completed_at?: string
  report?: ShoppingReport
  error?: string
  trace: TaskTraceEvent[]
}

export interface ShoppingTaskCreateResponse {
  success: boolean
  message: string
  task_id: string
}

export interface ShoppingTaskTraceResponse {
  success: boolean
  task_id: string
  trace: TaskTraceEvent[]
}
