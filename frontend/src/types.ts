/**
 * TypeScript domain contracts matching backend schemas.
 */

export type SectionType = 'hook' | 'setup' | 'middle' | 'climax' | 'takeaway';

export interface ScriptSection {
  id: string;
  type: string;
  heading: string;
  narration: string;
  notes?: string;
  visual_notes?: string;
  target_duration_seconds?: number;
}

export type ScriptStatus = 'draft' | 'final';

export interface Script {
  project_id: string;
  version: number;
  title: string;
  target_duration_minutes: number;
  status: ScriptStatus;
  sections: ScriptSection[];
  total_word_count?: number;
  estimated_duration_seconds?: number;
  notes?: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowState {
  accepted_version: number | null;
  rejected_version: number | null;
  rejection_reason: string | null;
}

export interface ScriptStateResponse {
  script: Script | null;
  workflow_state: WorkflowState;
}

export interface ConversationEvent {
  timestamp: string;
  stage: string;
  event_type: string;
  user_input?: string | null;
  system_output?: string | null;
  metadata?: Record<string, unknown>;
}

export interface StageMetadata {
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  version?: number | null;
}

export interface ProjectMetadata {
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  current_stage: string;
  stages: Record<string, StageMetadata>;
}

// ---------------------------------------------------------------------------
// Phase 6 Research Studio & Fact Checking Contracts
// ---------------------------------------------------------------------------

export type QueryStatus = 'planned' | 'completed' | 'skipped' | 'failed';

export interface QueryOutcomeView {
  query_id: string;
  query_text: string;
  purpose: string;
  target_question?: string | null;
  status: QueryStatus;
  result_count: number;
  error_message?: string | null;
  skip_reason?: string | null;
}

export interface SafeSourceView {
  source_id: string;
  url: string;
  title: string;
  publisher?: string | null;
  retrieval_status: string;
  retrieved_at?: string | null;
  publication_date?: string | null;
  char_count: number;
}

export interface SafeEvidenceView {
  evidence_id: string;
  source_id: string;
  query_id?: string | null;
  run_id?: string | null;
  excerpt: string;
  locator?: string | null;
  target_claim?: string | null;
  status: string; // 'retrieved' | 'candidate' | 'discarded'
  is_selected: boolean;
  selection_rationale?: string | null;
  is_used_in_current_script: boolean;
  used_by_claims: string[];
}

export interface ResearchOverviewResponse {
  project_id: string;
  has_research: boolean;
  active_run_id: string | null;
  status: string | null;
  termination_reason: string | null;
  budget_summary: {
    search_calls_used?: number;
    extract_calls_used?: number;
    total_chars_extracted?: number;
    max_search_calls?: number;
    max_extract_calls?: number;
    max_total_chars?: number;
  };
  queries: QueryOutcomeView[];
  sources: SafeSourceView[];
  selected_evidence: SafeEvidenceView[];
  retrieved_evidence_ids: string[];
  selected_evidence_ids: string[];
  used_evidence_ids: string[];
  unused_evidence_ids: string[];
  conflicts: string[];
  unresolved_questions: string[];
  disclaimer?: string;
}

export interface ResearchBriefResponse {
  project_id: string;
  run_id: string | null;
  brief_markdown: string;
}

export interface ClaimAssessment {
  claim_id: string;
  section_id: string;
  proposition: string;
  status: 'supported' | 'contradicted' | 'conflicting' | 'unassessed';
  risk_level: 'normal' | 'high';
  explanation: string;
  unassessed_reason?: string | null;
  considered_evidence_ids?: string[];
  supporting_evidence_ids?: string[];
  contradicting_evidence_ids?: string[];
}

export interface FactCheckSummary {
  total_claims: number;
  supported: number;
  contradicted: number;
  conflicting: number;
  unassessed: number;
  unassessed_reasons: Record<string, number>;
}

export interface ScriptFactCheckReport {
  report_id: string;
  project_id: string;
  script_version: number;
  research_run_id: string | null;
  checked_at: string;
  assessments: ClaimAssessment[];
  requires_repair: boolean;
  summary: FactCheckSummary;
}
