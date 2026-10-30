import React, { useState, useEffect, useCallback } from 'react';
import './styles.css';
import { api, ApiError } from './api';
import { ProjectMetadata, Script, WorkflowState, ConversationEvent } from './types';
import { ProjectBar } from './components/ProjectBar';
import { ScriptViewer } from './components/ScriptViewer';
import { ChatPanel } from './components/ChatPanel';
import { FinalizeModal } from './components/FinalizeModal';
import { RejectModal } from './components/RejectModal';
import { VersionsModal } from './components/VersionsModal';

export const App: React.FC = () => {
  const [projects, setProjects] = useState<ProjectMetadata[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [script, setScript] = useState<Script | null>(null);
  const [workflowState, setWorkflowState] = useState<WorkflowState>({
    accepted_version: null,
    rejected_version: null,
    rejection_reason: null,
  });
  const [history, setHistory] = useState<ConversationEvent[]>([]);

  // UI state
  const [apiOnline, setApiOnline] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [submittingModal, setSubmittingModal] = useState<boolean>(false);

  // Modals
  const [showFinalizeModal, setShowFinalizeModal] = useState<boolean>(false);
  const [showRejectModal, setShowRejectModal] = useState<boolean>(false);
  const [showVersionsModal, setShowVersionsModal] = useState<boolean>(false);

  // Check server health periodically
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        await api.checkHealth();
        if (active) setApiOnline(true);
      } catch {
        if (active) setApiOnline(false);
      }
    };
    check();
    const interval = setInterval(check, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  // Load project list on mount
  const refreshProjects = useCallback(async (preferredId?: string) => {
    try {
      const list = await api.listProjects();
      setApiOnline(true);
      setProjects(list);
      if (list.length > 0) {
        const nextId = preferredId || (currentProjectId && list.some((p) => p.project_id === currentProjectId)
          ? currentProjectId
          : list[0].project_id);
        setCurrentProjectId(nextId);
      } else {
        setCurrentProjectId(null);
      }
    } catch (err) {
      setApiOnline(false);
      setError(err instanceof ApiError ? err.detail : 'Failed to connect to local backend API.');
    }
  }, [currentProjectId]);

  useEffect(() => {
    refreshProjects();
  }, []);

  // Fetch script & history whenever active project changes
  const loadProjectData = useCallback(async (projectId: string) => {
    try {
      setLoading(true);
      setError(null);
      const [stateRes, histRes] = await Promise.all([
        api.getScriptState(projectId),
        api.getConversationHistory(projectId),
      ]);
      setScript(stateRes.script);
      setWorkflowState(stateRes.workflow_state);
      setHistory(histRes);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load script data.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentProjectId) {
      loadProjectData(currentProjectId);
    } else {
      setScript(null);
      setWorkflowState({ accepted_version: null, rejected_version: null, rejection_reason: null });
      setHistory([]);
    }
  }, [currentProjectId, loadProjectData]);

  // Project Creation
  const handleCreateProject = async (title: string) => {
    try {
      setError(null);
      const newProj = await api.createProject(title);
      await refreshProjects(newProj.project_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to create project.');
      throw err;
    }
  };

  // Script Actions
  const handleStartScript = async (topic: string, durationMinutes: number, title?: string) => {
    if (!currentProjectId) return;
    try {
      setLoading(true);
      setError(null);
      const res = await api.startScript(currentProjectId, topic, durationMinutes, title);
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      const hist = await api.getConversationHistory(currentProjectId);
      setHistory(hist);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to generate initial script.');
    } finally {
      setLoading(false);
    }
  };

  const handleReviseScript = async (instruction: string) => {
    if (!currentProjectId) return;
    try {
      setLoading(true);
      setError(null);
      const res = await api.reviseScript(currentProjectId, instruction);
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      const hist = await api.getConversationHistory(currentProjectId);
      setHistory(hist);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to revise script.');
    } finally {
      setLoading(false);
    }
  };

  const handleAcceptDraft = async () => {
    if (!currentProjectId) return;
    try {
      setLoading(true);
      setError(null);
      const res = await api.acceptDraft(currentProjectId);
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      const hist = await api.getConversationHistory(currentProjectId);
      setHistory(hist);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to accept draft.');
    } finally {
      setLoading(false);
    }
  };

  const handleRejectDraft = async (reason?: string) => {
    if (!currentProjectId) return;
    try {
      setSubmittingModal(true);
      setError(null);
      const res = await api.rejectDraft(currentProjectId, reason);
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      const hist = await api.getConversationHistory(currentProjectId);
      setHistory(hist);
      setShowRejectModal(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to reject draft.');
    } finally {
      setSubmittingModal(false);
    }
  };

  const handleFinalizeScript = async () => {
    if (!currentProjectId) return;
    try {
      setSubmittingModal(true);
      setError(null);
      const res = await api.finalizeScript(currentProjectId);
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      const hist = await api.getConversationHistory(currentProjectId);
      setHistory(hist);
      setShowFinalizeModal(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to finalize script.');
    } finally {
      setSubmittingModal(false);
    }
  };

  return (
    <div className="app-container">
      {/* Top Project Bar */}
      <ProjectBar
        projects={projects}
        currentProjectId={currentProjectId}
        onSelectProject={(id) => setCurrentProjectId(id)}
        onCreateProject={handleCreateProject}
        loading={loading}
        apiOnline={apiOnline}
      />

      {/* Error Banner */}
      {error && (
        <div className="banner banner-error">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span>⚠️</span>
            <span>{error}</span>
          </div>
          <button
            type="button"
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontWeight: 700 }}
            onClick={() => setError(null)}
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Studio Workspace */}
      <div className="main-content">
        {/* Left Column: Script Viewer */}
        <section className="script-column">
          {currentProjectId ? (
            <ScriptViewer
              script={script}
              workflowState={workflowState}
              loading={loading}
              onStartScript={handleStartScript}
              onAcceptDraft={handleAcceptDraft}
              onOpenRejectModal={() => setShowRejectModal(true)}
              onOpenFinalizeModal={() => setShowFinalizeModal(true)}
              onOpenVersionsModal={() => setShowVersionsModal(true)}
            />
          ) : (
            <div style={{ textAlign: 'center', margin: '4rem auto', maxWidth: '420px', color: 'var(--text-secondary)' }}>
              <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>📂</div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.5rem' }}>
                No Project Selected
              </h2>
              <p style={{ fontSize: '0.9rem', marginBottom: '1.5rem' }}>
                Create a project using the button in the top bar to start writing your explainer animation script.
              </p>
            </div>
          )}
        </section>

        {/* Right Column: Chat Revision Panel */}
        <aside className="chat-column">
          <ChatPanel
            events={history}
            script={script}
            loading={loading}
            onSendRevision={handleReviseScript}
          />
        </aside>
      </div>

      {/* Finalize Confirmation Modal */}
      {showFinalizeModal && script && (
        <FinalizeModal
          script={script}
          onConfirm={handleFinalizeScript}
          onCancel={() => setShowFinalizeModal(false)}
          submitting={submittingModal}
        />
      )}

      {/* Reject Modal */}
      {showRejectModal && script && (
        <RejectModal
          script={script}
          onConfirm={handleRejectDraft}
          onCancel={() => setShowRejectModal(false)}
          submitting={submittingModal}
        />
      )}

      {/* Versions & History Modal */}
      {showVersionsModal && currentProjectId && (
        <VersionsModal
          projectId={currentProjectId}
          onClose={() => setShowVersionsModal(false)}
        />
      )}
    </div>
  );
};

export default App;
