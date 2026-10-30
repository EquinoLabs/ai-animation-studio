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
