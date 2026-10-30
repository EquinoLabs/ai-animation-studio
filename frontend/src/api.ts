/**
 * API client for interacting with the backend FastAPI service.
 */

import {
  ProjectMetadata,
  Script,
  ScriptStateResponse,
  ConversationEvent,
  ResearchOverviewResponse,
  ResearchBriefResponse,
  ScriptFactCheckReport,
} from './types';

const API_BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const errorJson = await response.json();
      if (typeof errorJson.detail === 'string') {
        detail = errorJson.detail;
      } else if (Array.isArray(errorJson.detail)) {
        detail = errorJson.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join('; ');
      } else if (errorJson.message) {
        detail = errorJson.message;
      }
    } catch {
      // If parsing fails, fall back to status text
      detail = response.statusText || detail;
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  // Health
  checkHealth: async (): Promise<{ status: string; service: string }> => {
    const res = await fetch(`${API_BASE}/health`);
    return handleResponse(res);
  },

  // Projects
  listProjects: async (): Promise<ProjectMetadata[]> => {
    const res = await fetch(`${API_BASE}/projects`);
    return handleResponse<ProjectMetadata[]>(res);
  },

  createProject: async (title: string, projectId?: string): Promise<ProjectMetadata> => {
    const res = await fetch(`${API_BASE}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, project_id: projectId }),
    });
    return handleResponse<ProjectMetadata>(res);
  },

  getProject: async (projectId: string): Promise<ProjectMetadata> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}`);
    return handleResponse<ProjectMetadata>(res);
  },

  // Script
  getScriptState: async (projectId: string): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script`);
    return handleResponse<ScriptStateResponse>(res);
  },

  startScript: async (
    projectId: string,
    topic: string,
    targetDurationMinutes = 5.0,
    title?: string
  ): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic,
        target_duration_minutes: targetDurationMinutes,
        title: title || undefined,
      }),
    });
    return handleResponse<ScriptStateResponse>(res);
  },

  reviseScript: async (projectId: string, instruction: string): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/revise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    });
    return handleResponse<ScriptStateResponse>(res);
  },

  acceptDraft: async (projectId: string): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/accept`, {
      method: 'POST',
    });
    return handleResponse<ScriptStateResponse>(res);
  },

  rejectDraft: async (projectId: string, reason?: string): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason || undefined }),
    });
    return handleResponse<ScriptStateResponse>(res);
  },

  finalizeScript: async (projectId: string): Promise<ScriptStateResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/finalize`, {
      method: 'POST',
    });
    return handleResponse<ScriptStateResponse>(res);
  },

  getConversationHistory: async (projectId: string): Promise<ConversationEvent[]> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/history`);
    return handleResponse<ConversationEvent[]>(res);
  },

  getScriptVersions: async (projectId: string): Promise<Script[]> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/versions`);
    return handleResponse<Script[]>(res);
  },

  getFinalizedVersions: async (projectId: string): Promise<Script[]> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/finalized`);
    return handleResponse<Script[]>(res);
  },

  // Research Studio & Fact-Checking (Phase 6 Checkpoint 1 & 2)
  getResearchOverview: async (projectId: string): Promise<ResearchOverviewResponse> => {
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/research`);
    return handleResponse<ResearchOverviewResponse>(res);
  },

  getResearchBrief: async (projectId: string, runId?: string): Promise<ResearchBriefResponse> => {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : '';
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/research/brief${query}`);
    return handleResponse<ResearchBriefResponse>(res);
  },

  getFactCheckReport: async (projectId: string, version?: number): Promise<ScriptFactCheckReport> => {
    const query = version !== undefined ? `?version=${encodeURIComponent(version)}` : '';
    const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/script/fact-check${query}`);
    return handleResponse<ScriptFactCheckReport>(res);
  },
};
