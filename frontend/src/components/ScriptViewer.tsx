import React, { useState } from 'react';
import {
  Script,
  ScriptClaim,
  WorkflowState,
  ScriptFactCheckReport,
  ResearchOverviewResponse,
  ChallengeAction,
  ChallengeSummary,
  ClaimAssessment,
} from '../types';
import { ClaimInspectionModal } from './ClaimInspectionModal';

interface ScriptViewerProps {
  script: Script | null;
  workflowState: WorkflowState;
  loading: boolean;
  factCheckReport?: ScriptFactCheckReport | null;
  researchOverview?: ResearchOverviewResponse | null;
  onStartScript: (topic: string, durationMinutes: number, title?: string) => Promise<void>;
  onAcceptDraft: () => Promise<void>;
  onOpenRejectModal: () => void;
  onOpenFinalizeModal: () => void;
  onOpenVersionsModal: () => void;
  onChallengeClaim?: (claimId: string, action: ChallengeAction, instruction?: string) => Promise<void>;
  challengingClaim?: boolean;
  challengeResult?: ChallengeSummary | null;
}

export const ScriptViewer: React.FC<ScriptViewerProps> = ({
  script,
  workflowState,
  loading,
  factCheckReport,
  researchOverview,
  onStartScript,
  onAcceptDraft,
  onOpenRejectModal,
  onOpenFinalizeModal,
  onOpenVersionsModal,
  onChallengeClaim,
  challengingClaim = false,
  challengeResult,
}) => {
  // Initial draft form state
  const [topic, setTopic] = useState('');
  const [duration, setDuration] = useState(5.0);
  const [customTitle, setCustomTitle] = useState('');

  // Selected claim for detail inspection & challenge modal
  const [selectedClaim, setSelectedClaim] = useState<ScriptClaim | null>(null);

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
            {script.duration_compliance ? (
              <div
                style={{
                  marginTop: '0.6rem',
                  padding: '0.45rem 0.75rem',
                  borderRadius: '6px',
                  fontSize: '0.8rem',
                  display: 'flex',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '0.6rem',
                  backgroundColor:
                    script.duration_compliance.compliance_status === 'compliant'
                      ? 'rgba(34, 197, 94, 0.1)'
                      : script.duration_compliance.compliance_status === 'under_target_evidence_deficit'
                      ? 'rgba(234, 179, 8, 0.12)'
                      : script.duration_compliance.compliance_status === 'under_target_generator_shortfall'
                      ? 'rgba(239, 68, 68, 0.12)'
                      : 'rgba(168, 85, 247, 0.12)',
                  border: `1px solid ${
                    script.duration_compliance.compliance_status === 'compliant'
                      ? 'rgba(34, 197, 94, 0.35)'
                      : script.duration_compliance.compliance_status === 'under_target_evidence_deficit'
                      ? 'rgba(234, 179, 8, 0.4)'
                      : script.duration_compliance.compliance_status === 'under_target_generator_shortfall'
                      ? 'rgba(239, 68, 68, 0.4)'
                      : 'rgba(168, 85, 247, 0.4)'
                  }`,
                }}
              >
                <span
                  style={{
                    fontWeight: 700,
                    color:
                      script.duration_compliance.compliance_status === 'compliant'
                        ? '#22c55e'
                        : script.duration_compliance.compliance_status === 'under_target_evidence_deficit'
                        ? '#eab308'
                        : script.duration_compliance.compliance_status === 'under_target_generator_shortfall'
                        ? '#f87171'
                        : '#c084fc',
                  }}
                >
                  {script.duration_compliance.compliance_status === 'compliant'
                    ? '✓ Duration Target Met'
                    : script.duration_compliance.compliance_status === 'under_target_evidence_deficit'
                    ? '⚠ Under-Target: Evidence Deficit'
                    : script.duration_compliance.compliance_status === 'under_target_generator_shortfall'
                    ? '⚠ Under-Target: Generator Shortfall'
                    : '⚠ Under-Target: Undetermined Cause'}
                </span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {script.duration_compliance.actual_words} actual words / {script.duration_compliance.target_words} target
                  {script.duration_compliance.deficit_words > 0 && (
                    <strong style={{ color: '#f87171', marginLeft: '0.35rem' }}>
                      (-{script.duration_compliance.deficit_words} word shortfall)
                    </strong>
                  )}
                </span>
                {script.duration_compliance.assessment_note && (
                  <span
                    style={{
                      fontSize: '0.75rem',
                      color: 'var(--text-muted)',
                      flexBasis: '100%',
                      marginTop: '0.2rem',
                    }}
                  >
                    {script.duration_compliance.assessment_note}
                  </span>
                )}
              </div>
            ) : null}
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

                {/* Discrete Factual Claims Annotation Block */}
                {(() => {
                  const sectionClaimIds = sec.claim_ids || [];
                  const activeSectionClaims = (script.claims || []).filter(
                    (c) => c.section_id === sec.id && (sectionClaimIds.length === 0 || sectionClaimIds.includes(c.claim_id))
                  );

                  return (
                    <div style={{ marginBottom: '1rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                        <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                          ⚖️ Factual Claims & Evidence Linkage ({activeSectionClaims.length})
                        </div>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          Click claim to inspect grounding or challenge
                        </span>
                      </div>

                      {activeSectionClaims.length === 0 ? (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontStyle: 'italic', padding: '0.5rem 0.75rem', backgroundColor: 'var(--bg-card)', borderRadius: 'var(--radius-sm)' }}>
                          No discrete factual assertions declared in this section narration.
                        </div>
                      ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          {activeSectionClaims.map((claim) => {
                            const assessment: ClaimAssessment | undefined = factCheckReport?.assessments.find(
                              (a) => a.claim_id === claim.claim_id
                            );
                            const statusKey = assessment?.status || claim.assessment_status || 'not_checked';
                            const riskKey = assessment?.risk_level || claim.risk_level || 'normal';

                            const statusBadgeStyles: Record<string, { bg: string; text: string; border: string; label: string }> = {
                              supported: { bg: 'rgba(16, 185, 129, 0.15)', text: '#34d399', border: 'rgba(16, 185, 129, 0.35)', label: 'Supported' },
                              partially_supported: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: 'rgba(245, 158, 11, 0.35)', label: 'Partially Supported' },
                              contradicted: { bg: 'rgba(244, 63, 94, 0.15)', text: '#fb7185', border: 'rgba(244, 63, 94, 0.35)', label: 'Contradicted' },
                              conflicting: { bg: 'rgba(249, 115, 22, 0.15)', text: '#fb923c', border: 'rgba(249, 115, 22, 0.35)', label: 'Conflicting' },
                              unsupported: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: 'rgba(245, 158, 11, 0.35)', label: 'Unsupported' },
                              not_checked: { bg: 'rgba(148, 163, 184, 0.15)', text: '#cbd5e1', border: 'rgba(148, 163, 184, 0.25)', label: 'Not Checked' },
                            };
                            const sBadge = statusBadgeStyles[statusKey] || statusBadgeStyles.not_checked;

                            const isUnlinked = claim.link_status === 'unlinked';

                            return (
                              <div
                                key={claim.claim_id}
                                className="claim-card"
                                onClick={() => setSelectedClaim(claim)}
                                style={{
                                  padding: '0.65rem 0.85rem',
                                  backgroundColor: isUnlinked ? 'rgba(51, 65, 85, 0.25)' : 'var(--bg-card)',
                                  border: `1px solid ${sBadge.border}`,
                                  borderRadius: 'var(--radius-sm)',
                                  cursor: 'pointer',
                                  transition: 'all 0.15s ease',
                                }}
                              >
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.25rem' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                                    <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', fontWeight: 700, color: 'var(--accent-blue)' }}>
                                      [{claim.claim_id}]
                                    </span>
                                    <span
                                      className="badge"
                                      style={{
                                        backgroundColor: sBadge.bg,
                                        color: sBadge.text,
                                        border: `1px solid ${sBadge.border}`,
                                        fontSize: '0.7rem',
                                      }}
                                    >
                                      {sBadge.label}
                                    </span>
                                    {riskKey === 'high' && (
                                      <span
                                        className="badge"
                                        style={{
                                          backgroundColor: 'rgba(244, 63, 94, 0.15)',
                                          color: '#fb7185',
                                          border: '1px solid rgba(244, 63, 94, 0.35)',
                                          fontSize: '0.68rem',
                                        }}
                                      >
                                        High Risk
                                      </span>
                                    )}
                                    {riskKey === 'creative_flexibility' && (
                                      <span
                                        className="badge"
                                        style={{
                                          backgroundColor: 'rgba(168, 85, 247, 0.15)',
                                          color: '#c084fc',
                                          border: '1px solid rgba(168, 85, 247, 0.35)',
                                          fontSize: '0.68rem',
                                        }}
                                      >
                                        Creative Flexibility
                                      </span>
                                    )}
                                    {isUnlinked && (
                                      <span className="badge badge-secondary" style={{ fontSize: '0.68rem' }}>
                                        Unlinked
                                      </span>
                                    )}
                                  </div>

                                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                                    <span>{claim.evidence_ids.length} evidence ref{claim.evidence_ids.length === 1 ? '' : 's'}</span>
                                    <span style={{ color: 'var(--accent-indigo)', fontWeight: 600 }}>Inspect ↗</span>
                                  </div>
                                </div>

                                <div style={{ fontSize: '0.825rem', color: isUnlinked ? 'var(--text-muted)' : 'var(--text-primary)', lineHeight: '1.4' }}>
                                  “{claim.claim_text}”
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })()}

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

      {/* Claim Detail Inspection and Challenge Modal */}
      {selectedClaim && (
        <ClaimInspectionModal
          claim={selectedClaim}
          sectionHeading={script.sections.find((s) => s.id === selectedClaim.section_id)?.heading}
          assessment={factCheckReport?.assessments.find((a) => a.claim_id === selectedClaim.claim_id)}
          evidenceRecords={researchOverview?.selected_evidence}
          sources={researchOverview?.sources}
          onClose={() => setSelectedClaim(null)}
          onChallenge={async (action, instruction) => {
            if (onChallengeClaim) {
              await onChallengeClaim(selectedClaim.claim_id, action, instruction);
            }
          }}
          challenging={challengingClaim}
          challengeResult={challengeResult}
        />
      )}
    </div>
  );
};
