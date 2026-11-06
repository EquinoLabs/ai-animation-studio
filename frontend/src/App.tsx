import React, { useState, useEffect, useCallback } from 'react';
import './styles.css';
import { api, ApiError } from './api';
import {
  ProjectMetadata,
  Script,
  WorkflowState,
  ConversationEvent,
  ScriptFactCheckReport,
  ResearchOverviewResponse,
  ChallengeAction,
  ChallengeSummary,
} from './types';
import { ProjectBar } from './components/ProjectBar';
import { ScriptViewer } from './components/ScriptViewer';
import { ResearchStudioPanel } from './components/ResearchStudioPanel';
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

  // Phase 6 Checkpoint 3 Fact-Checking & Challenge state
  const [factCheckReport, setFactCheckReport] = useState<ScriptFactCheckReport | null>(null);
  const [researchOverview, setResearchOverview] = useState<ResearchOverviewResponse | null>(null);
  const [challengingClaim, setChallengingClaim] = useState<boolean>(false);
  const [challengeResult, setChallengeResult] = useState<ChallengeSummary | null>(null);

  // Modals
  const [showFinalizeModal, setShowFinalizeModal] = useState<boolean>(false);
  const [showRejectModal, setShowRejectModal] = useState<boolean>(false);
  const [showVersionsModal, setShowVersionsModal] = useState<boolean>(false);

  // Active workspace view: Script editor or Research Studio (Phase 6 Checkpoint 2)
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState<'script' | 'research'>('script');

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
      const [stateRes, histRes, repRes, ovRes] = await Promise.all([
        api.getScriptState(projectId),
        api.getConversationHistory(projectId),
        api.getFactCheckReport(projectId).catch(() => null),
        api.getResearchOverview(projectId).catch(() => null),
      ]);
      setScript(stateRes.script);
      setWorkflowState(stateRes.workflow_state);
      setHistory(histRes);
      setFactCheckReport(repRes);
      setResearchOverview(ovRes);
      setChallengeResult(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load script data.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentProjectId) {
      // Clear previous project data immediately to prevent stale cross-project display
      setScript(null);
      setWorkflowState({ accepted_version: null, rejected_version: null, rejection_reason: null });
      setHistory([]);
      setFactCheckReport(null);
      setResearchOverview(null);
      setChallengeResult(null);
      loadProjectData(currentProjectId);
    } else {
      setScript(null);
      setWorkflowState({ accepted_version: null, rejected_version: null, rejection_reason: null });
      setHistory([]);
      setFactCheckReport(null);
      setResearchOverview(null);
      setChallengeResult(null);
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
      const [hist, rep, ov] = await Promise.all([
        api.getConversationHistory(currentProjectId),
        api.getFactCheckReport(currentProjectId).catch(() => null),
        api.getResearchOverview(currentProjectId).catch(() => null),
      ]);
      setHistory(hist);
      setFactCheckReport(rep);
      if (ov) setResearchOverview(ov);
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
      const [hist, rep, ov] = await Promise.all([
        api.getConversationHistory(currentProjectId),
        api.getFactCheckReport(currentProjectId).catch(() => null),
        api.getResearchOverview(currentProjectId).catch(() => null),
      ]);
      setHistory(hist);
      setFactCheckReport(rep);
      if (ov) setResearchOverview(ov);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to revise script.');
    } finally {
      setLoading(false);
    }
  };

  const handleChallengeClaim = async (
    claimId: string,
    action: ChallengeAction,
    instruction?: string
  ) => {
    if (!currentProjectId) return;
    try {
      setChallengingClaim(true);
      setError(null);
      const res = await api.challengeClaim(currentProjectId, {
        claim_id: claimId,
        action,
        instruction,
      });
      setScript(res.script);
      setWorkflowState(res.workflow_state);
      setFactCheckReport(res.fact_check_report || null);
      setChallengeResult(res.challenge_summary);

      const [hist, ov] = await Promise.all([
        api.getConversationHistory(currentProjectId),
        api.getResearchOverview(currentProjectId).catch(() => null),
      ]);
      setHistory(hist);
      if (ov) setResearchOverview(ov);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Claim challenge failed.');
      throw err;
    } finally {
      setChallengingClaim(false);
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

      {/* Workspace Tabs Navigation Bar (Phase 6 Checkpoint 2) */}
      {currentProjectId && (
        <div className="workspace-tabs-bar">
          <nav className="workspace-nav" aria-label="Studio Workspace Navigation">
            <button
              type="button"
              className={`workspace-tab-btn ${activeWorkspaceTab === 'script' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('script')}
            >
              📝 Script Workspace
              {script && (
                <span className="tab-pill">
                  v{script.version} ({script.status})
                </span>
              )}
            </button>
            <button
              type="button"
              className={`workspace-tab-btn ${activeWorkspaceTab === 'research' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('research')}
            >
              🔬 Research Studio
            </button>
          </nav>
        </div>
      )}

      {/* Main Studio Workspace */}
      <div className="main-content">
        {/* Left Column: Script Viewer or Research Studio */}
        <section className="script-column">
          {currentProjectId ? (
            activeWorkspaceTab === 'script' ? (
              <ScriptViewer
                script={script}
                workflowState={workflowState}
                loading={loading}
                factCheckReport={factCheckReport}
                researchOverview={researchOverview}
                onStartScript={handleStartScript}
                onAcceptDraft={handleAcceptDraft}
                onOpenRejectModal={() => setShowRejectModal(true)}
                onOpenFinalizeModal={() => setShowFinalizeModal(true)}
                onOpenVersionsModal={() => setShowVersionsModal(true)}
                onChallengeClaim={handleChallengeClaim}
                challengingClaim={challengingClaim}
                challengeResult={challengeResult}
              />
            ) : (
              <ResearchStudioPanel
                projectId={currentProjectId}
                projectTitle={projects.find((p) => p.project_id === currentProjectId)?.title}
                currentScriptVersion={script?.version}
                onResearchCompleted={() => loadProjectData(currentProjectId)}
              />
            )
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
            onChallengeClaim={handleChallengeClaim}
            factCheckReport={factCheckReport}
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
