import React, { useState } from 'react';
import { Script, WorkflowState } from '../types';

interface ScriptViewerProps {
  script: Script | null;
  workflowState: WorkflowState;
  loading: boolean;
  onStartScript: (topic: string, durationMinutes: number, title?: string) => Promise<void>;
  onAcceptDraft: () => Promise<void>;
  onOpenRejectModal: () => void;
  onOpenFinalizeModal: () => void;
  onOpenVersionsModal: () => void;
}

export const ScriptViewer: React.FC<ScriptViewerProps> = ({
  script,
  workflowState,
  loading,
  onStartScript,
  onAcceptDraft,
  onOpenRejectModal,
  onOpenFinalizeModal,
  onOpenVersionsModal,
}) => {
  // Initial draft form state
  const [topic, setTopic] = useState('');
  const [duration, setDuration] = useState(5.0);
  const [customTitle, setCustomTitle] = useState('');

  const handleStartSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    onStartScript(topic.trim(), duration, customTitle.trim() || undefined);
  };

  // If no script exists yet, display generation prompt card
  if (!script) {
    return (
      <div style={{ maxWidth: '680px', margin: '2rem auto' }}>
        <div
          style={{
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-lg)',
            padding: '2rem',
            boxShadow: '0 10px 25px -5px rgba(0,0,0,0.3)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem' }}>
            <span style={{ fontSize: '2rem' }}>✨</span>
            <div>
              <h2 style={{ fontSize: '1.35rem', fontWeight: 700 }}>Start New Explainer Script</h2>
              <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                Provide a topic or idea. The local LLM will generate a structured multi-scene narrative draft.
              </p>
            </div>
          </div>

          <form onSubmit={handleStartSubmit}>
            <div style={{ marginBottom: '1.25rem' }}>
              <label style={{ display: 'block', fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.4rem' }}>
                Topic / Subject <span style={{ color: 'var(--accent-rose)' }}>*</span>
              </label>
              <textarea
                placeholder="e.g. How does a quantum computer differ from a classical supercomputer? Focus on qubits, superposition, and quantum entanglement."
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                rows={4}
                required
                disabled={loading}
                style={{ width: '100%', resize: 'vertical' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1.5rem' }}>
              <div>
                <label style={{ display: 'block', fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.4rem' }}>
                  Target Duration
                </label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <input
                    type="number"
                    min="1"
                    max="30"
                    step="0.5"
                    value={duration}
                    onChange={(e) => setDuration(parseFloat(e.target.value) || 5.0)}
                    disabled={loading}
                    style={{ width: '100%' }}
                  />
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>min</span>
                </div>
              </div>

              <div>
                <label style={{ display: 'block', fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.4rem' }}>
                  Preferred Title (optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Quantum Computing Explained"
                  value={customTitle}
                  onChange={(e) => setCustomTitle(e.target.value)}
                  disabled={loading}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <button
              type="submit"
              className="btn-primary"
              disabled={loading || !topic.trim()}
              style={{ width: '100%', padding: '0.75rem', fontSize: '0.95rem', fontWeight: 600 }}
            >
              {loading ? 'Generating Initial Draft...' : 'Generate Script Draft'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // Calculate stats
  const isAccepted = workflowState.accepted_version === script.version;
  const isRejected = workflowState.rejected_version === script.version;
  const isFinal = script.status === 'final';

  let statusPillClass = 'pill-draft';
  let statusText = 'Draft (Unreviewed)';
  if (isFinal) {
    statusPillClass = 'pill-final';
    statusText = `Finalized (v${String(script.version).padStart(3, '0')})`;
  } else if (isAccepted) {
    statusPillClass = 'pill-accepted';
    statusText = 'Accepted (Ready to Finalize)';
  } else if (isRejected) {
    statusPillClass = 'pill-rejected';
    statusText = 'Rejected (Needs Revision)';
  }

  const totalWords = script.sections.reduce((acc, s) => acc + s.narration.split(/\s+/).filter(Boolean).length, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Script Header Card */}
      <div
        style={{
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-md)',
          padding: '1.5rem',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.4rem' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  backgroundColor: 'var(--bg-tertiary)',
                  padding: '0.2rem 0.5rem',
                  borderRadius: '4px',
                  color: 'var(--accent-blue)',
                }}
              >
                v{script.version}
              </span>
              <span className={`pill ${statusPillClass}`}>{statusText}</span>
              {isFinal && (
                <span style={{ fontSize: '0.75rem', color: '#c084fc' }}>
                  🔒 Locked Downstream Artifact
                </span>
              )}
            </div>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 700, letterSpacing: '-0.01em' }}>{script.title}</h2>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
              {script.target_duration_minutes} min target (~{((script.estimated_duration_seconds || (totalWords / (140 / 60))) / 60).toFixed(1)} min est.) • {script.sections.length} scene sections • ~{script.total_word_count ?? totalWords} narration words
            </div>
          </div>

          {/* Action Toolbar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={onOpenVersionsModal}
              title="Inspect history and versions"
            >
              📜 History / Versions
            </button>

            {!isFinal && (
              <>
                <button
                  type="button"
                  className={isAccepted ? 'btn-secondary' : 'btn-success'}
                  onClick={onAcceptDraft}
                  disabled={loading || isAccepted}
                  title={isAccepted ? 'Draft is already accepted' : 'Accept this draft'}
                >
                  ✓ {isAccepted ? 'Accepted' : 'Accept Draft'}
                </button>

                <button
                  type="button"
                  className={isRejected ? 'btn-secondary' : 'btn-danger'}
                  onClick={onOpenRejectModal}
                  disabled={loading || isRejected}
                  title="Reject this draft and provide feedback"
                >
                  ✕ {isRejected ? 'Rejected' : 'Reject Draft'}
                </button>

                <button
                  type="button"
                  className="btn-finalize"
                  onClick={onOpenFinalizeModal}
                  disabled={loading || !isAccepted}
                  title={!isAccepted ? 'Draft must be accepted before finalization' : 'Finalize this draft'}
                >
                  🔒 Finalize Script
                </button>
              </>
            )}
          </div>
        </div>

        {/* Workflow guidance alerts */}
        {isRejected && (
          <div
            style={{
              marginTop: '1rem',
              padding: '0.75rem 1rem',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'rgba(244, 63, 94, 0.1)',
              border: '1px solid rgba(244, 63, 94, 0.3)',
              fontSize: '0.85rem',
              color: '#fecdd3',
            }}
          >
            <strong>Draft Rejected:</strong> {workflowState.rejection_reason || 'No specific reason given.'} Use the chat
            assistant on the right to enter your revision instructions.
          </div>
        )}

        {isAccepted && !isFinal && (
          <div
            style={{
              marginTop: '1rem',
              padding: '0.75rem 1rem',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'rgba(16, 185, 129, 0.1)',
              border: '1px solid rgba(16, 185, 129, 0.3)',
              fontSize: '0.85rem',
              color: '#a7f3d0',
            }}
          >
            <strong>Draft Accepted:</strong> This draft is verified and ready. Click <strong>Finalize Script</strong> to lock
            it as the immutable artifact for Scene Planning and Voiceover.
          </div>
        )}

        {isFinal && (
          <div
            style={{
              marginTop: '1rem',
              padding: '0.75rem 1rem',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'rgba(99, 102, 241, 0.1)',
              border: '1px solid rgba(99, 102, 241, 0.3)',
              fontSize: '0.85rem',
              color: '#c7d2fe',
            }}
          >
            <strong>Script Finalized:</strong> This version is saved to <code>script/finalized/v{String(script.version).padStart(3, '0')}.json</code>.
            You may continue revising in the chat panel to create a new draft version without affecting this finalized output.
          </div>
        )}
      </div>

      {/* Sections List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <h3 style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Script Structure ({script.sections.length} Sections)
        </h3>

        {script.sections.map((sec, index) => {
          let roleColor = '#38bdf8';
          if (sec.type === 'hook') roleColor = '#f59e0b';
          if (sec.type === 'setup') roleColor = '#38bdf8';
          if (sec.type === 'middle') roleColor = '#818cf8';
          if (sec.type === 'climax') roleColor = '#f43f5e';
          if (sec.type === 'takeaway') roleColor = '#10b981';

          return (
            <div
              key={sec.id || index}
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                overflow: 'hidden',
                transition: 'border-color 0.15s ease',
              }}
            >
              {/* Section Header */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '0.75rem 1.25rem',
                  backgroundColor: 'var(--bg-card)',
                  borderBottom: '1px solid var(--border-color)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <span
                    style={{
                      fontSize: '0.7rem',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      padding: '0.2rem 0.5rem',
                      borderRadius: '4px',
                      backgroundColor: 'rgba(255,255,255,0.06)',
                      color: roleColor,
                      border: `1px solid ${roleColor}40`,
                    }}
                  >
                    {sec.type}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{sec.heading}</span>
                </div>

                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  Section {index + 1} of {script.sections.length} • ~{sec.target_duration_seconds ?? Math.round(sec.narration.split(/\s+/).filter(Boolean).length / (140 / 60))}s
                </div>
              </div>

              {/* Section Body */}
              <div style={{ padding: '1.25rem' }}>
                {/* Narration Block */}
                <div style={{ marginBottom: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.4rem' }}>
                    🎙️ Voiceover Narration
                  </div>
                  <p
                    style={{
                      fontSize: '0.95rem',
                      lineHeight: 1.65,
                      color: 'var(--text-primary)',
                      whiteSpace: 'pre-wrap',
                      backgroundColor: 'var(--bg-primary)',
                      padding: '1rem',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-color)',
                    }}
                  >
                    {sec.narration}
                  </p>
                </div>

                {/* Visual Notes Block */}
                {(sec.visual_notes || sec.notes) && (
                  <div>
                    <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.3rem' }}>
                      🎬 Visual & Anime Scene Notes
                    </div>
                    <div
                      style={{
                        fontSize: '0.85rem',
                        color: 'var(--text-secondary)',
                        fontStyle: 'italic',
                        backgroundColor: 'rgba(56, 189, 248, 0.05)',
                        borderLeft: '3px solid var(--accent-blue)',
                        padding: '0.6rem 0.9rem',
                        borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
                      }}
                    >
                      {sec.visual_notes || sec.notes}
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
