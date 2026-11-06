import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { api, ApiError } from '../api';
import {
  ResearchOverviewResponse,
  ScriptFactCheckReport,
} from '../types';
import { ResearchBriefModal } from './ResearchBriefModal';

interface ResearchStudioPanelProps {
  projectId: string;
  projectTitle?: string;
  currentScriptVersion?: number;
  onResearchCompleted?: () => void;
}

type StudioTab = 'evidence' | 'queries' | 'sources' | 'assessments';
type EvidenceFilter = 'all' | 'candidates' | 'selected' | 'used' | 'unused';
type QueryFilter = 'all' | 'completed' | 'planned' | 'skipped' | 'failed';

export const ResearchStudioPanel: React.FC<ResearchStudioPanelProps> = ({
  projectId,
  projectTitle,
  currentScriptVersion,
  onResearchCompleted,
}) => {
  const [overview, setOverview] = useState<ResearchOverviewResponse | null>(null);
  const [report, setReport] = useState<ScriptFactCheckReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StudioTab>('evidence');
  const [showBriefModal, setShowBriefModal] = useState<boolean>(false);

  // Automated Research initiation state
  const [researchTopic, setResearchTopic] = useState<string>(projectTitle || '');
  const [researching, setResearching] = useState<boolean>(false);
  const [initError, setInitError] = useState<string | null>(null);
  const [showStartForm, setShowStartForm] = useState<boolean>(false);

  // Evidence filtering & search
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceFilter>('all');
  const [evidenceSearch, setEvidenceSearch] = useState<string>('');

  // Query lifecycle filtering
  const [queryFilter, setQueryFilter] = useState<QueryFilter>('all');

  useEffect(() => {
    if (projectTitle && !researchTopic) {
      setResearchTopic(projectTitle);
    }
  }, [projectTitle]);

  const handleInitiateResearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!researchTopic.trim() || researching) return;
    setResearching(true);
    setInitError(null);
    try {
      const res = await api.startResearch(projectId, { topic: researchTopic.trim() });
      setOverview(res.overview);
      try {
        const repRes = await api.getFactCheckReport(projectId, currentScriptVersion);
        setReport(repRes);
      } catch {
        setReport(null);
      }
      setShowStartForm(false);
      if (onResearchCompleted) {
        onResearchCompleted();
      }
    } catch (err) {
      setInitError(err instanceof ApiError ? err.detail : 'Failed to execute research pipeline.');
    } finally {
      setResearching(false);
    }
  };

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setOverview(null); // Clear previous project data immediately to prevent stale display
    setReport(null);
    try {
      const ovRes = await api.getResearchOverview(projectId);
      setOverview(ovRes);

      try {
        const repRes = await api.getFactCheckReport(projectId, currentScriptVersion);
        setReport(repRes);
      } catch {
        // Fact check report may be absent if draft was not yet checked or unresearched
        setReport(null);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load research studio data.');
    } finally {
      setLoading(false);
    }
  }, [projectId, currentScriptVersion]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Query counts and filtered queries
  const queryCounts = useMemo(() => {
    if (!overview || !overview.queries) {
      return { all: 0, completed: 0, planned: 0, skipped: 0, failed: 0 };
    }
    const qs = overview.queries;
    return {
      all: qs.length,
      completed: qs.filter((q) => q.status === 'completed').length,
      planned: qs.filter((q) => q.status === 'planned').length,
      skipped: qs.filter((q) => q.status === 'skipped').length,
      failed: qs.filter((q) => q.status === 'failed').length,
    };
  }, [overview]);

  const filteredQueries = useMemo(() => {
    if (!overview || !overview.queries) return [];
    if (queryFilter === 'all') return overview.queries;
    return overview.queries.filter((q) => q.status === queryFilter);
  }, [overview, queryFilter]);

  // Filtered evidence items
  const filteredEvidence = useMemo(() => {
    if (!overview || !overview.selected_evidence) return [];

    let list = overview.selected_evidence;

    // Stage filter
    if (evidenceFilter === 'candidates') {
      list = list.filter((e) => e.status === 'candidate');
    } else if (evidenceFilter === 'selected') {
      list = list.filter((e) => e.is_selected);
    } else if (evidenceFilter === 'used') {
      list = list.filter((e) => e.is_used_in_current_script);
    } else if (evidenceFilter === 'unused') {
      list = list.filter((e) => !e.is_used_in_current_script);
    }

    // Search filter
    if (evidenceSearch.trim()) {
      const q = evidenceSearch.toLowerCase();
      list = list.filter(
        (e) =>
          e.evidence_id.toLowerCase().includes(q) ||
          e.excerpt.toLowerCase().includes(q) ||
          (e.target_claim && e.target_claim.toLowerCase().includes(q)) ||
          (e.locator && e.locator.toLowerCase().includes(q)) ||
          (e.selection_rationale && e.selection_rationale.toLowerCase().includes(q))
      );
    }

    return list;
  }, [overview, evidenceFilter, evidenceSearch]);

  // Counts for pills
  const counts = useMemo(() => {
    if (!overview || !overview.selected_evidence) {
      return { all: 0, candidates: 0, selected: 0, used: 0, unused: 0 };
    }
    const all = overview.selected_evidence;
    return {
      all: all.length,
      candidates: all.filter((e) => e.status === 'candidate').length,
      selected: all.filter((e) => e.is_selected).length,
      used: all.filter((e) => e.is_used_in_current_script).length,
      unused: all.filter((e) => !e.is_used_in_current_script).length,
    };
  }, [overview]);

  if (loading && !overview) {
    return (
      <div className="research-panel-loading" style={{ textAlign: 'center', padding: '5rem 2rem' }}>
        <div className="spinner" style={{ margin: '0 auto 1.5rem' }} />
        <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Loading Research Studio...</h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          Aggregating queries, verified sources, and grounded evidence.
        </p>
      </div>
    );
  }

  if (error && !overview) {
    return (
      <div style={{ maxWidth: '600px', margin: '4rem auto', padding: '0 1rem' }}>
        <div className="banner banner-error" style={{ marginBottom: '1.5rem' }}>
          <span>⚠️ {error}</span>
        </div>
        <button type="button" className="btn-primary" onClick={loadData}>
          🔄 Retry Connection
        </button>
      </div>
    );
  }

  // Graceful handling for project without research
  if (!overview || !overview.has_research) {
    return (
      <div className="research-empty-state" style={{ maxWidth: '640px', margin: '4rem auto', textAlign: 'center' }}>
        <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🔬</div>
        <h2 style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: '0.6rem' }}>
          No Research Runs Found
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', lineHeight: '1.6', marginBottom: '1.5rem' }}>
          This project has not completed an automated research cycle yet. When you generate a research-grounded script draft,
          the system will execute queries, extract source facts, record evidence excerpts, and ground all claims here.
        </p>

        {/* Clean Research Initiation Form */}
        <form
          onSubmit={handleInitiateResearch}
          style={{
            margin: '1.5rem auto',
            maxWidth: '480px',
            textAlign: 'left',
            backgroundColor: 'var(--bg-secondary)',
            padding: '1.25rem',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-color)',
          }}
        >
          <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
            Investigate Topic:
          </label>
          <input
            type="text"
            className="form-input"
            value={researchTopic}
            onChange={(e) => setResearchTopic(e.target.value)}
            placeholder="Enter research topic..."
            disabled={researching}
            style={{ width: '100%', marginBottom: '0.75rem', padding: '0.5rem 0.75rem' }}
            required
          />
          {initError && (
            <div className="banner banner-error" style={{ marginBottom: '0.75rem', fontSize: '0.825rem' }}>
              <span>⚠️ {initError}</span>
            </div>
          )}
          <button
            type="submit"
            className="btn-primary"
            disabled={researching || !researchTopic.trim()}
            style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}
          >
            {researching ? (
              <>
                <div className="spinner-sm" /> Running Research Pipeline...
              </>
            ) : (
              '🚀 Start Automated Research'
            )}
          </button>
        </form>

        <div className="banner banner-info" style={{ textAlign: 'left', fontSize: '0.85rem' }}>
          <span>💡 <strong>Tip:</strong> Head over to the Script Workspace to create or revise a draft with automated research grounding.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="research-studio-container" style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '1.25rem' }}>
      {/* 1. Header & Run Status */}
      <header
        className="research-header-card"
        style={{
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-md)',
          padding: '1.25rem 1.5rem',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.35rem' }}>
              <h2 style={{ fontSize: '1.3rem', fontWeight: 700 }}>🔬 Research Studio</h2>
              <span
                className={`badge badge-${overview.status === 'completed' ? 'success' : overview.status === 'failed' ? 'danger' : 'warning'}`}
                style={{ textTransform: 'uppercase', fontSize: '0.7rem', letterSpacing: '0.04em' }}
              >
                Run {overview.status || 'Active'}
              </span>
              {overview.active_run_id && (
                <span style={{ fontSize: '0.75rem', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                  ID: {overview.active_run_id}
                </span>
              )}
            </div>
            <p style={{ fontSize: '0.825rem', color: 'var(--text-secondary)' }}>
              Transparent evidence catalog, search queries, verified sources, and claim grounding.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setShowStartForm(!showStartForm)}
              style={{ fontSize: '0.8rem', padding: '0.45rem 0.85rem' }}
            >
              {showStartForm ? '✕ Close Form' : '🔍 Run Research'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowBriefModal(true)}
              style={{ fontSize: '0.8rem', padding: '0.45rem 0.85rem' }}
            >
              📑 View Evidence Brief
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={loadData}
              disabled={loading}
              title="Refresh research data"
              style={{ fontSize: '0.8rem', padding: '0.45rem 0.75rem' }}
            >
              🔄 Refresh
            </button>
          </div>
        </div>

        {/* Optional Collapsible Research Form */}
        {showStartForm && (
          <form
            onSubmit={handleInitiateResearch}
            style={{
              marginTop: '1rem',
              padding: '1rem',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border-color)',
            }}
          >
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
              Investigate New Topic:
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <input
                type="text"
                className="form-input"
                value={researchTopic}
                onChange={(e) => setResearchTopic(e.target.value)}
                placeholder="Enter topic..."
                disabled={researching}
                style={{ flex: 1, minWidth: '240px', padding: '0.45rem 0.75rem' }}
                required
              />
              <button
                type="submit"
                className="btn-primary"
                disabled={researching || !researchTopic.trim()}
                style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
              >
                {researching ? (
                  <>
                    <div className="spinner-sm" /> Running...
                  </>
                ) : (
                  '🚀 Start Run'
                )}
              </button>
            </div>
            {initError && (
              <div className="banner banner-error" style={{ marginTop: '0.5rem', fontSize: '0.825rem' }}>
                <span>⚠️ {initError}</span>
              </div>
            )}
          </form>
        )}

        {/* Objective Truth & Recheck Disclaimer Notice */}
        <div
          className="research-disclaimer-banner"
          style={{
            marginTop: '1rem',
            padding: '0.65rem 0.9rem',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'rgba(56, 189, 248, 0.08)',
            border: '1px solid rgba(56, 189, 248, 0.25)',
            fontSize: '0.78rem',
            lineHeight: '1.45',
            color: '#bae6fd',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.5rem',
          }}
        >
          <span style={{ fontSize: '1rem', lineHeight: '1' }}>ℹ️</span>
          <div>
            <strong>Grounded Verification Notice:</strong> Evidence excerpts and fact-checking evaluations reflect
            local AI-assisted grounding against retrieved source documents. They do not certify objective external truth,
            and rechecking existing catalogs does not constitute fresh external research.
          </div>
        </div>

        {/* Budget & Resource Metrics */}
        <div
          className="budget-metrics-bar"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '0.75rem',
            marginTop: '1rem',
            paddingTop: '0.85rem',
            borderTop: '1px solid var(--border-color)',
          }}
        >
          <div className="metric-box">
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Search Calls</span>
            <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-primary)' }}>
              {overview.budget_summary.search_calls_used ?? 0}
              {overview.budget_summary.max_search_calls && (
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}> / {overview.budget_summary.max_search_calls}</span>
              )}
            </div>
          </div>
          <div className="metric-box">
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Extract Calls</span>
            <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-primary)' }}>
              {overview.budget_summary.extract_calls_used ?? 0}
              {overview.budget_summary.max_extract_calls && (
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}> / {overview.budget_summary.max_extract_calls}</span>
              )}
            </div>
          </div>
          <div className="metric-box">
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Chars Extracted</span>
            <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-primary)' }}>
              {(overview.budget_summary.total_chars_extracted ?? 0).toLocaleString()} chars
            </div>
          </div>
          <div className="metric-box">
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Verified Sources</span>
            <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--accent-blue)' }}>
              {overview.sources.length} sources
            </div>
          </div>
        </div>
      </header>

      {/* 2. Navigation Tabs */}
      <div
        className="research-subnav"
        style={{
          display: 'flex',
          gap: '0.5rem',
          borderBottom: '1px solid var(--border-color)',
          paddingBottom: '0.5rem',
          flexWrap: 'wrap',
        }}
      >
        <button
          type="button"
          className={`tab-btn ${activeTab === 'evidence' ? 'active' : ''}`}
          onClick={() => setActiveTab('evidence')}
          style={{
            padding: '0.45rem 0.9rem',
            fontSize: '0.85rem',
            borderRadius: 'var(--radius-sm)',
            border: activeTab === 'evidence' ? '1px solid var(--accent-indigo)' : '1px solid transparent',
            backgroundColor: activeTab === 'evidence' ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
            color: activeTab === 'evidence' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
        >
          🔍 Evidence Catalog <span className="pill-badge">{counts.all}</span>
        </button>

        <button
          type="button"
          className={`tab-btn ${activeTab === 'queries' ? 'active' : ''}`}
          onClick={() => setActiveTab('queries')}
          style={{
            padding: '0.45rem 0.9rem',
            fontSize: '0.85rem',
            borderRadius: 'var(--radius-sm)',
            border: activeTab === 'queries' ? '1px solid var(--accent-indigo)' : '1px solid transparent',
            backgroundColor: activeTab === 'queries' ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
            color: activeTab === 'queries' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
        >
          📋 Query History <span className="pill-badge">{overview.queries.length}</span>
        </button>

        <button
          type="button"
          className={`tab-btn ${activeTab === 'sources' ? 'active' : ''}`}
          onClick={() => setActiveTab('sources')}
          style={{
            padding: '0.45rem 0.9rem',
            fontSize: '0.85rem',
            borderRadius: 'var(--radius-sm)',
            border: activeTab === 'sources' ? '1px solid var(--accent-indigo)' : '1px solid transparent',
            backgroundColor: activeTab === 'sources' ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
            color: activeTab === 'sources' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
        >
          🌐 Sources <span className="pill-badge">{overview.sources.length}</span>
        </button>

        <button
          type="button"
          className={`tab-btn ${activeTab === 'assessments' ? 'active' : ''}`}
          onClick={() => setActiveTab('assessments')}
          style={{
            padding: '0.45rem 0.9rem',
            fontSize: '0.85rem',
            borderRadius: 'var(--radius-sm)',
            border: activeTab === 'assessments' ? '1px solid var(--accent-indigo)' : '1px solid transparent',
            backgroundColor: activeTab === 'assessments' ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
            color: activeTab === 'assessments' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
        >
          ⚖️ Assessments & Conflicts{' '}
          {overview.conflicts.length > 0 && (
            <span className="pill-badge" style={{ backgroundColor: 'rgba(244, 63, 94, 0.25)', color: '#fda4af' }}>
              {overview.conflicts.length}
            </span>
          )}
        </button>
      </div>

      {/* 3. Tab Contents */}
      <div className="research-tab-content" style={{ flex: 1, overflowY: 'auto' }}>
        {/* ========================================================================= */}
        {/* Tab 1: Evidence Catalog */}
        {/* ========================================================================= */}
        {activeTab === 'evidence' && (
          <div className="evidence-catalog-view" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Visual Distinction Helper Guide */}
            <div
              className="evidence-guide-box"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-sm)',
                padding: '0.75rem 1rem',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
                display: 'flex',
                flexWrap: 'wrap',
                gap: '1rem',
                alignItems: 'center',
              }}
            >
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Evidence Stages:</span>
              <span><strong>Retrieved:</strong> Persisted in research catalog</span>
              <span><strong>Candidate:</strong> Available for narrative consideration</span>
              <span><strong>Selected:</strong> Explicitly chosen by outliner</span>
              <span><strong>Actively Used:</strong> Grounding narration claim</span>
            </div>

            {/* Filter Pills & Search Input */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
              <div className="filter-pill-group" style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className={`pill-button ${evidenceFilter === 'all' ? 'active' : ''}`}
                  onClick={() => setEvidenceFilter('all')}
                >
                  All ({counts.all})
                </button>
                <button
                  type="button"
                  className={`pill-button ${evidenceFilter === 'candidates' ? 'active' : ''}`}
                  onClick={() => setEvidenceFilter('candidates')}
                  title="Evidence available for narrative consideration"
                >
                  Candidates ({counts.candidates})
                </button>
                <button
                  type="button"
                  className={`pill-button ${evidenceFilter === 'selected' ? 'active' : ''}`}
                  onClick={() => setEvidenceFilter('selected')}
                  title="Evidence explicitly chosen by outline sections"
                >
                  Selected ({counts.selected})
                </button>
                <button
                  type="button"
                  className={`pill-button ${evidenceFilter === 'used' ? 'active' : ''}`}
                  onClick={() => setEvidenceFilter('used')}
                  title="Evidence actively cited by current script narration claims"
                >
                  Actively Used ({counts.used})
                </button>
                <button
                  type="button"
                  className={`pill-button ${evidenceFilter === 'unused' ? 'active' : ''}`}
                  onClick={() => setEvidenceFilter('unused')}
                  title="Evidence available in catalog but not cited in narration"
                >
                  Unused in Script ({counts.unused})
                </button>
              </div>

              <div style={{ minWidth: '220px' }}>
                <input
                  type="text"
                  placeholder="Filter by keyword or ID..."
                  value={evidenceSearch}
                  onChange={(e) => setEvidenceSearch(e.target.value)}
                  style={{ width: '100%', padding: '0.4rem 0.75rem', fontSize: '0.825rem' }}
                />
              </div>
            </div>

            {/* Evidence Items List */}
            {filteredEvidence.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-secondary)' }}>
                <p>No evidence records matching the current filter criteria.</p>
              </div>
            ) : (
              <div className="evidence-grid" style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                {filteredEvidence.map((evi) => {
                  return (
                    <article
                      key={evi.evidence_id}
                      className="evidence-card"
                      style={{
                        backgroundColor: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-md)',
                        padding: '1.1rem 1.25rem',
                        transition: 'border-color 0.15s ease',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.75rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 700, fontFamily: 'monospace', fontSize: '0.85rem', color: 'var(--accent-blue)' }}>
                            [{evi.evidence_id}]
                          </span>

                          {/* Candidate / Retrieved Status Tag */}
                          <span
                            className="badge"
                            style={{
                              backgroundColor: evi.status === 'candidate' ? 'rgba(245, 158, 11, 0.15)' : 'rgba(148, 163, 184, 0.15)',
                              color: evi.status === 'candidate' ? '#fbbf24' : '#cbd5e1',
                              border: `1px solid ${evi.status === 'candidate' ? 'rgba(245, 158, 11, 0.35)' : 'rgba(148, 163, 184, 0.25)'}`,
                              fontSize: '0.72rem',
                            }}
                          >
                            {evi.status === 'candidate' ? 'Candidate for Consideration' : 'Retrieved Excerpt'}
                          </span>

                          {/* Outliner Selection Badge */}
                          <span
                            className="badge"
                            style={{
                              backgroundColor: evi.is_selected ? 'rgba(168, 85, 247, 0.15)' : 'transparent',
                              color: evi.is_selected ? '#c084fc' : 'var(--text-muted)',
                              border: `1px solid ${evi.is_selected ? 'rgba(168, 85, 247, 0.35)' : 'var(--border-color)'}`,
                              fontSize: '0.72rem',
                            }}
                          >
                            {evi.is_selected ? '✨ AI-Selected by Outline' : '⚪ Not Selected by Outline'}
                          </span>

                          {/* Script Narration Usage Badge */}
                          <span
                            className="badge"
                            style={{
                              backgroundColor: evi.is_used_in_current_script ? 'rgba(16, 185, 129, 0.15)' : 'rgba(100, 116, 139, 0.12)',
                              color: evi.is_used_in_current_script ? '#34d399' : '#94a3b8',
                              border: `1px solid ${evi.is_used_in_current_script ? 'rgba(16, 185, 129, 0.35)' : 'rgba(100, 116, 139, 0.25)'}`,
                              fontSize: '0.72rem',
                            }}
                          >
                            {evi.is_used_in_current_script
                              ? `📜 Actively Used in Narration (${evi.used_by_claims.join(', ') || 'Linked'})`
                              : '💤 Unused in Current Narration'}
                          </span>
                        </div>

                        {evi.locator && (
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            📍 {evi.locator}
                          </span>
                        )}
                      </div>

                      {/* Excerpt Quote */}
                      <blockquote
                        style={{
                          margin: '0.5rem 0',
                          padding: '0.75rem 1rem',
                          backgroundColor: 'var(--bg-primary)',
                          borderLeft: '3px solid var(--accent-blue)',
                          borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
                          fontSize: '0.875rem',
                          lineHeight: '1.5',
                          color: 'var(--text-primary)',
                          fontStyle: 'normal',
                        }}
                      >
                        “{evi.excerpt}”
                      </blockquote>

                      {/* Target Claim / Research Question */}
                      {evi.target_claim && (
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                          🎯 <strong>Addressed Question/Claim:</strong> {evi.target_claim}
                        </div>
                      )}

                      {/* Selection Rationale: Truthful display without synthetic hallucination */}
                      {evi.selection_rationale ? (
                        <div
                          style={{
                            marginTop: '0.65rem',
                            padding: '0.5rem 0.75rem',
                            backgroundColor: 'rgba(99, 102, 241, 0.08)',
                            border: '1px solid rgba(99, 102, 241, 0.25)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '0.78rem',
                            color: '#c7d2fe',
                          }}
                        >
                          💡 <strong>Selection Rationale:</strong> {evi.selection_rationale}
                        </div>
                      ) : (
                        <div style={{ marginTop: '0.4rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                          <em>No selection rationale recorded for this item.</em>
                        </div>
                      )}

                      {/* Source ID Anchor */}
                      <div style={{ marginTop: '0.6rem', fontSize: '0.72rem', color: 'var(--text-muted)', display: 'flex', gap: '0.75rem' }}>
                        <span>Source: <code>{evi.source_id}</code></span>
                        {evi.query_id && <span>Query: <code>{evi.query_id}</code></span>}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ========================================================================= */}
        {/* Tab 2: Query History */}
        {/* ========================================================================= */}
        {activeTab === 'queries' && (
          <div className="queries-history-view" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div
              className="query-header-notice"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-sm)',
                padding: '0.75rem 1rem',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
              }}
            >
              Exposes all planned search queries, their lifecycle state (completed, planned, skipped, failed),
              and recorded skip reasons safely.
            </div>

            {/* Query Lifecycle Filter Pills */}
            <div className="filter-pill-group" style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className={`pill-button ${queryFilter === 'all' ? 'active' : ''}`}
                onClick={() => setQueryFilter('all')}
              >
                All ({queryCounts.all})
              </button>
              <button
                type="button"
                className={`pill-button ${queryFilter === 'completed' ? 'active' : ''}`}
                onClick={() => setQueryFilter('completed')}
              >
                Completed ({queryCounts.completed})
              </button>
              <button
                type="button"
                className={`pill-button ${queryFilter === 'planned' ? 'active' : ''}`}
                onClick={() => setQueryFilter('planned')}
              >
                Planned ({queryCounts.planned})
              </button>
              <button
                type="button"
                className={`pill-button ${queryFilter === 'skipped' ? 'active' : ''}`}
                onClick={() => setQueryFilter('skipped')}
              >
                Skipped ({queryCounts.skipped})
              </button>
              <button
                type="button"
                className={`pill-button ${queryFilter === 'failed' ? 'active' : ''}`}
                onClick={() => setQueryFilter('failed')}
              >
                Failed ({queryCounts.failed})
              </button>
            </div>

            {filteredQueries.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '2rem 0' }}>
                No queries matching the selected &quot;{queryFilter}&quot; status filter.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                {filteredQueries.map((q) => {
                  const statusColors: Record<string, { bg: string; text: string; border: string }> = {
                    completed: { bg: 'rgba(16, 185, 129, 0.15)', text: '#34d399', border: 'rgba(16, 185, 129, 0.3)' },
                    planned: { bg: 'rgba(56, 189, 248, 0.15)', text: '#38bdf8', border: 'rgba(56, 189, 248, 0.3)' },
                    skipped: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: 'rgba(245, 158, 11, 0.3)' },
                    failed: { bg: 'rgba(244, 63, 94, 0.15)', text: '#fb7185', border: 'rgba(244, 63, 94, 0.3)' },
                  };
                  const colors = statusColors[q.status] || statusColors.planned;

                  return (
                    <article
                      key={q.query_id}
                      style={{
                        backgroundColor: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-md)',
                        padding: '1.1rem 1.25rem',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.5rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                          <span
                            className="badge"
                            style={{
                              backgroundColor: colors.bg,
                              color: colors.text,
                              border: `1px solid ${colors.border}`,
                              textTransform: 'uppercase',
                              fontSize: '0.7rem',
                            }}
                          >
                            {q.status}
                          </span>
                          <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                            {q.query_id}
                          </span>
                        </div>

                        {q.status === 'completed' && (
                          <span style={{ fontSize: '0.8rem', color: 'var(--accent-green)' }}>
                            ✓ {q.result_count} {q.result_count === 1 ? 'source' : 'sources'} retrieved
                          </span>
                        )}
                      </div>

                      <h4 style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
                        “{q.query_text}”
                      </h4>

                      <div style={{ fontSize: '0.825rem', color: 'var(--text-secondary)', marginBottom: '0.35rem' }}>
                        <strong>Objective:</strong> {q.purpose}
                      </div>

                      {q.target_question && (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.35rem' }}>
                          🎯 <strong>Target Question:</strong> {q.target_question}
                        </div>
                      )}

                      {/* Explicit Skip Reason Display */}
                      {q.status === 'skipped' && q.skip_reason && (
                        <div
                          style={{
                            marginTop: '0.6rem',
                            padding: '0.5rem 0.75rem',
                            backgroundColor: 'rgba(245, 158, 11, 0.1)',
                            border: '1px solid rgba(245, 158, 11, 0.3)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '0.78rem',
                            color: '#fef08a',
                          }}
                        >
                          ⚠️ <strong>Skip Reason:</strong> {q.skip_reason}
                        </div>
                      )}

                      {/* Safe Error Display */}
                      {q.status === 'failed' && q.error_message && (
                        <div
                          style={{
                            marginTop: '0.6rem',
                            padding: '0.5rem 0.75rem',
                            backgroundColor: 'rgba(244, 63, 94, 0.1)',
                            border: '1px solid rgba(244, 63, 94, 0.3)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '0.78rem',
                            color: '#fecdd3',
                          }}
                        >
                          ✕ <strong>Failure:</strong> {q.error_message}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ========================================================================= */}
        {/* Tab 3: Sources */}
        {/* ========================================================================= */}
        {activeTab === 'sources' && (
          <div className="sources-catalog-view" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div
              className="sources-guide-box"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-sm)',
                padding: '0.75rem 1rem',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
              }}
            >
              Verified extracted sources. All outbound links use strict <code>http://</code> / <code>https://</code> protocols
              with safe origin isolation (<code>rel=&quot;noopener noreferrer&quot;</code>). Internal server paths are never exposed.
            </div>

            {overview.sources.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '2rem 0' }}>
                No external sources recorded for this run.
              </p>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
                {overview.sources.map((src) => {
                  const isSafeUrl = src.url.startsWith('http://') || src.url.startsWith('https://');

                  return (
                    <article
                      key={src.source_id}
                      style={{
                        backgroundColor: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-md)',
                        padding: '1.1rem 1.25rem',
                        display: 'flex',
                        flexDirection: 'column',
                        justifyContent: 'space-between',
                        gap: '0.75rem',
                      }}
                    >
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                          <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            {src.source_id}
                          </span>
                          <span
                            className="badge badge-success"
                            style={{ textTransform: 'capitalize', fontSize: '0.7rem' }}
                          >
                            {src.retrieval_status}
                          </span>
                        </div>

                        <h4 style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.4rem', lineHeight: '1.4' }}>
                          {src.title || 'Untitled Source'}
                        </h4>

                        {src.publisher && (
                          <div style={{ fontSize: '0.8rem', color: 'var(--accent-blue)', marginBottom: '0.35rem' }}>
                            🏢 {src.publisher}
                          </div>
                        )}

                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          📄 {src.char_count ? `${src.char_count.toLocaleString()} chars` : 'Full content extracted'}
                          {src.publication_date && ` • Published ${src.publication_date}`}
                        </div>
                      </div>

                      {isSafeUrl ? (
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn-secondary"
                          style={{
                            textAlign: 'center',
                            fontSize: '0.8rem',
                            padding: '0.4rem 0.75rem',
                            textDecoration: 'none',
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: '0.4rem',
                          }}
                        >
                          Visit Source ↗
                        </a>
                      ) : (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          URL protocol not permitted
                        </span>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ========================================================================= */}
        {/* Tab 4: Assessments, Conflicts & Unresolved Questions */}
        {/* ========================================================================= */}
        {activeTab === 'assessments' && (
          <div className="assessments-view" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            {/* Fact Check Report Overview */}
            {report ? (
              <div
                className="fact-check-summary-card"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  padding: '1.25rem',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
                  <div>
                    <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Fact-Check Report (Version {report.script_version})</h3>
                    <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                      Evaluated on {new Date(report.created_at || report.checked_at || Date.now()).toLocaleString()}
                    </p>
                  </div>
                  <span
                    className={`badge badge-${report.requires_repair ? 'danger' : 'success'}`}
                    style={{ fontSize: '0.78rem', padding: '0.35rem 0.75rem' }}
                  >
                    {report.requires_repair ? '⚠️ Requires Repair' : '✓ All Claims Supported'}
                  </span>
                </div>

                {/* Metrics Breakdown */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
                    gap: '0.75rem',
                  }}
                >
                  <div className="metric-box">
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Total Claims</span>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                      {report.total_claims ?? report.summary?.total_claims ?? report.assessments.length}
                    </div>
                  </div>
                  <div className="metric-box">
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-green)' }}>Supported</span>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--accent-green)' }}>
                      {report.supported_count ?? report.summary?.supported ?? 0}
                    </div>
                  </div>
                  <div className="metric-box">
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-rose)' }}>Contradicted</span>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--accent-rose)' }}>
                      {report.contradicted_count ?? report.summary?.contradicted ?? 0}
                    </div>
                  </div>
                  <div className="metric-box">
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-amber)' }}>Conflicting</span>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--accent-amber)' }}>
                      {report.conflicting_count ?? report.summary?.conflicting ?? 0}
                    </div>
                  </div>
                  <div className="metric-box">
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Unassessed / Not Checked</span>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                      {report.not_checked_count ?? report.unsupported_count ?? report.summary?.unassessed ?? 0}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  padding: '1.25rem',
                  textAlign: 'center',
                  color: 'var(--text-secondary)',
                  fontSize: '0.85rem',
                }}
              >
                No fact-check report found for the current script version.
              </div>
            )}

            {/* Synthesized Conflicts */}
            <div
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                padding: '1.25rem',
              }}
            >
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span>⚡</span> Synthesized Conflicts & Contradictions ({overview.conflicts.length})
              </h3>
              {overview.conflicts.length === 0 ? (
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  No contradictions or evidence conflicts detected across evaluated claims.
                </p>
              ) : (
                <ul style={{ paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {overview.conflicts.map((conf, idx) => (
                    <li key={idx} style={{ fontSize: '0.85rem', color: '#fca5a5' }}>
                      {conf}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Unresolved Questions */}
            <div
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                padding: '1.25rem',
              }}
            >
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span>❓</span> Unresolved Research Questions ({overview.unresolved_questions.length})
              </h3>
              {overview.unresolved_questions.length === 0 ? (
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  All planned research questions received completed query extractions.
                </p>
              ) : (
                <ul style={{ paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {overview.unresolved_questions.map((unres, idx) => (
                    <li key={idx} style={{ fontSize: '0.85rem', color: '#fde047' }}>
                      {unres}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Detailed Assessments List if Report Available */}
            {report && report.assessments.length > 0 && (
              <div
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  padding: '1.25rem',
                }}
              >
                <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.85rem' }}>
                  Claim Assessment Details ({report.assessments.length})
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {report.assessments.map((a) => {
                    const statusBadgeColors: Record<string, string> = {
                      supported: 'success',
                      contradicted: 'danger',
                      conflicting: 'warning',
                      unassessed: 'secondary',
                    };

                    return (
                      <div
                        key={a.claim_id}
                        style={{
                          backgroundColor: 'var(--bg-primary)',
                          border: '1px solid var(--border-color)',
                          borderRadius: 'var(--radius-sm)',
                          padding: '0.85rem 1rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem', marginBottom: '0.4rem' }}>
                          <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            [{a.claim_id}] Section: {a.section_id}
                          </span>
                          <span
                            className={`badge badge-${statusBadgeColors[a.status] || 'secondary'}`}
                            style={{ textTransform: 'capitalize', fontSize: '0.7rem' }}
                          >
                            {a.status}
                          </span>
                        </div>

                        <p style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
                          “{a.proposition}”
                        </p>

                        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                          {a.explanation}
                        </p>

                        {(a.supporting_evidence_ids && a.supporting_evidence_ids.length > 0) && (
                          <div style={{ marginTop: '0.4rem', fontSize: '0.75rem', color: 'var(--accent-green)' }}>
                            Supporting evidence: {a.supporting_evidence_ids.join(', ')}
                          </div>
                        )}

                        {(a.contradicting_evidence_ids && a.contradicting_evidence_ids.length > 0) && (
                          <div style={{ marginTop: '0.4rem', fontSize: '0.75rem', color: 'var(--accent-rose)' }}>
                            Contradicting evidence: {a.contradicting_evidence_ids.join(', ')}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Brief Modal */}
      {showBriefModal && overview && (
        <ResearchBriefModal
          projectId={projectId}
          runId={overview.active_run_id}
          onClose={() => setShowBriefModal(false)}
        />
      )}
    </div>
  );
};
