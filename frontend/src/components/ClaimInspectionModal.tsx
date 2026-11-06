import React, { useState } from 'react';
import {
  ScriptClaim,
  ClaimAssessment,
  SafeEvidenceView,
  SafeSourceView,
  ChallengeAction,
  ChallengeSummary,
} from '../types';

interface ClaimInspectionModalProps {
  claim: ScriptClaim;
  sectionHeading?: string;
  assessment?: ClaimAssessment | null;
  evidenceRecords?: SafeEvidenceView[];
  sources?: SafeSourceView[];
  onClose: () => void;
  onChallenge: (action: ChallengeAction, instruction?: string) => Promise<void>;
  challenging: boolean;
  challengeResult?: ChallengeSummary | null;
}

export const ClaimInspectionModal: React.FC<ClaimInspectionModalProps> = ({
  claim,
  sectionHeading,
  assessment,
  evidenceRecords = [],
  sources = [],
  onClose,
  onChallenge,
  challenging,
  challengeResult,
}) => {
  const [selectedAction, setSelectedAction] = useState<ChallengeAction>('investigate');
  const [instruction, setInstruction] = useState<string>('');
  const [challengeError, setChallengeError] = useState<string | null>(null);

  // Status mapping
  const statusColors: Record<string, { bg: string; text: string; border: string; label: string }> = {
    supported: { bg: 'rgba(16, 185, 129, 0.15)', text: '#34d399', border: 'rgba(16, 185, 129, 0.35)', label: 'Supported' },
    partially_supported: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: 'rgba(245, 158, 11, 0.35)', label: 'Partially Supported' },
    contradicted: { bg: 'rgba(244, 63, 94, 0.15)', text: '#fb7185', border: 'rgba(244, 63, 94, 0.35)', label: 'Contradicted' },
    conflicting: { bg: 'rgba(249, 115, 22, 0.15)', text: '#fb923c', border: 'rgba(249, 115, 22, 0.35)', label: 'Conflicting Evidence' },
    unsupported: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: 'rgba(245, 158, 11, 0.35)', label: 'Unsupported' },
    not_checked: { bg: 'rgba(148, 163, 184, 0.15)', text: '#cbd5e1', border: 'rgba(148, 163, 184, 0.25)', label: 'Not Checked' },
  };

  const statusKey = assessment?.status || claim.assessment_status || 'not_checked';
  const statusInfo = statusColors[statusKey] || statusColors.not_checked;

  const riskColors: Record<string, { bg: string; text: string; border: string; label: string }> = {
    high: { bg: 'rgba(244, 63, 94, 0.15)', text: '#fb7185', border: 'rgba(244, 63, 94, 0.35)', label: 'High Risk' },
    normal: { bg: 'rgba(56, 189, 248, 0.15)', text: '#38bdf8', border: 'rgba(56, 189, 248, 0.35)', label: 'Normal Scrutiny' },
    creative_flexibility: { bg: 'rgba(168, 85, 247, 0.15)', text: '#c084fc', border: 'rgba(168, 85, 247, 0.35)', label: 'Creative Flexibility' },
  };

  const riskKey = assessment?.risk_level || claim.risk_level || 'normal';
  const riskInfo = riskColors[riskKey] || riskColors.normal;

  // Resolve supporting and considered evidence records
  const supportingIds = assessment?.supporting_evidence_ids || claim.evidence_ids || [];
  const contradictingIds = assessment?.contradicting_evidence_ids || [];

  const evidenceMap = new Map<string, SafeEvidenceView>(
    evidenceRecords.map((e) => [e.evidence_id, e])
  );
  const sourceMap = new Map<string, SafeSourceView>(
    sources.map((s) => [s.source_id, s])
  );

  const handleSubmitChallenge = async (e: React.FormEvent) => {
    e.preventDefault();
    setChallengeError(null);
    try {
      await onChallenge(selectedAction, instruction.trim() || undefined);
    } catch (err: any) {
      setChallengeError(err?.message || 'Challenge request failed.');
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card modal-lg"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '820px', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
      >
        {/* Modal Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
            <span style={{ fontSize: '1.4rem' }}>🔍</span>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <h3 style={{ fontSize: '1.15rem', fontWeight: 600 }}>Claim Inspector</h3>
                <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--accent-blue)' }}>
                  [{claim.claim_id}]
                </span>
                {claim.link_status === 'unlinked' && (
                  <span className="badge badge-secondary" style={{ fontSize: '0.7rem' }}>
                    Unlinked / Removed
                  </span>
                )}
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Section: {sectionHeading || claim.section_id} ({claim.section_id})
              </p>
            </div>
          </div>
          <button
            type="button"
            className="btn-icon"
            onClick={onClose}
            aria-label="Close modal"
            style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: '1.2rem', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body" style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem', display: 'flex', flexDirection: 'column', gap: '1.2rem' }}>
          {/* Proposition Text */}
          <div
            style={{
              padding: '1rem',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
            }}
          >
            <div style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.35rem' }}>
              Factual Assertion Proposition
            </div>
            <p style={{ fontSize: '1.05rem', fontWeight: 500, lineHeight: '1.5', color: 'var(--text-primary)' }}>
              “{claim.claim_text}”
            </p>
          </div>

          {/* Assessment & Risk Badges */}
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {/* Status */}
            <span
              className="badge"
              style={{
                backgroundColor: statusInfo.bg,
                color: statusInfo.text,
                border: `1px solid ${statusInfo.border}`,
                fontSize: '0.78rem',
                padding: '0.3rem 0.7rem',
              }}
            >
              Assessment: {statusInfo.label}
            </span>

            {/* Risk */}
            <span
              className="badge"
              style={{
                backgroundColor: riskInfo.bg,
                color: riskInfo.text,
                border: `1px solid ${riskInfo.border}`,
                fontSize: '0.78rem',
                padding: '0.3rem 0.7rem',
              }}
            >
              Risk: {riskInfo.label}
            </span>

            {/* Linkage */}
            <span
              className="badge badge-secondary"
              style={{ fontSize: '0.78rem', padding: '0.3rem 0.7rem' }}
            >
              Linkage: {claim.link_status} ({supportingIds.length} evidence ref{supportingIds.length === 1 ? '' : 's'})
            </span>
          </div>

          {/* Grounding Explanation */}
          <div
            style={{
              padding: '0.85rem 1rem',
              backgroundColor: 'var(--bg-card)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-color)',
            }}
          >
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>
              Fact-Check Assessment Explanation
            </div>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-primary)', lineHeight: '1.5' }}>
              {assessment?.explanation || (
                claim.assessment_status === 'not_checked'
                  ? 'Claim has not yet been verified against evidence. Unresearched or newly drafted proposition.'
                  : 'No detailed assessment explanation generated.'
              )}
            </p>
            {assessment?.unassessed_reason && (
              <div style={{ marginTop: '0.4rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Unassessed reason: {assessment.unassessed_reason}
              </div>
            )}
          </div>

          {/* Fact-checking Objective Truth Disclaimer */}
          <div
            style={{
              padding: '0.65rem 0.85rem',
              backgroundColor: 'rgba(56, 189, 248, 0.08)',
              border: '1px solid rgba(56, 189, 248, 0.25)',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.75rem',
              color: '#bae6fd',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.45rem',
            }}
          >
            <span>ℹ️</span>
            <div>
              <strong>Grounding Notice:</strong> This assessment evaluates factual entailment against retrieved
              source excerpts. It does not certify absolute external truth.
            </div>
          </div>

          {/* Linked Supporting Evidence */}
          <div>
            <h4 style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
              Linked Evidence Citations ({supportingIds.length})
            </h4>

            {supportingIds.length === 0 ? (
              <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                No evidence citations linked to this claim.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {supportingIds.map((eid) => {
                  const ev = evidenceMap.get(eid);
                  const src = ev ? sourceMap.get(ev.source_id) : undefined;
                  const isSafeUrl = src?.url && (src.url.startsWith('http://') || src.url.startsWith('https://'));

                  return (
                    <div
                      key={eid}
                      style={{
                        padding: '0.75rem 1rem',
                        backgroundColor: 'var(--bg-primary)',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                        <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--accent-blue)', fontWeight: 600 }}>
                          [{eid}]
                        </span>
                        {ev?.locator && (
                          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                            📍 {ev.locator}
                          </span>
                        )}
                      </div>

                      {ev ? (
                        <>
                          <blockquote style={{ fontSize: '0.85rem', color: 'var(--text-primary)', fontStyle: 'italic', marginBottom: '0.4rem' }}>
                            “{ev.excerpt}”
                          </blockquote>
                          {src && (
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', marginTop: '0.4rem' }}>
                              <span style={{ color: 'var(--text-muted)' }}>
                                Source: <strong>{src.publisher || src.title || src.source_id}</strong>
                              </span>
                              {isSafeUrl && (
                                <a
                                  href={src.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ color: 'var(--accent-blue)', textDecoration: 'none' }}
                                >
                                  View Source ↗
                                </a>
                              )}
                            </div>
                          )}
                        </>
                      ) : (
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          Evidence record cataloged in research run.
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Contradicting Evidence If Present */}
          {contradictingIds.length > 0 && (
            <div
              style={{
                padding: '0.85rem 1rem',
                backgroundColor: 'rgba(244, 63, 94, 0.08)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              <h4 style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--accent-rose)', textTransform: 'uppercase', marginBottom: '0.4rem' }}>
                ⚠️ Contradicting Evidence Citations ({contradictingIds.length})
              </h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {contradictingIds.map((cid) => {
                  const ev = evidenceMap.get(cid);
                  return (
                    <div key={cid} style={{ fontSize: '0.8rem', color: '#fecdd3' }}>
                      <strong>[{cid}]:</strong> {ev?.excerpt || 'Contradicting excerpt from research run.'}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Interactive Conversational Challenge Form */}
          <div
            style={{
              marginTop: '0.5rem',
              padding: '1.1rem',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--accent-indigo)',
              borderRadius: 'var(--radius-md)',
            }}
          >
            <h4 style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span>⚖️</span> Challenge This Claim Conversationally
            </h4>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '0.85rem' }}>
              Target: <code style={{ color: 'var(--accent-blue)' }}>{claim.claim_id}</code>. Accidental challenges to other claims are prevented.
            </p>

            <form onSubmit={handleSubmitChallenge}>
              {/* Action Tabs */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: '0.4rem', marginBottom: '0.85rem' }}>
                <button
                  type="button"
                  className={`pill-button ${selectedAction === 'investigate' ? 'active' : ''}`}
                  onClick={() => setSelectedAction('investigate')}
                  disabled={challenging}
                  title="Targeted re-investigation or existing evidence recheck"
                >
                  🔍 Investigate
                </button>
                <button
                  type="button"
                  className={`pill-button ${selectedAction === 'qualify' ? 'active' : ''}`}
                  onClick={() => setSelectedAction('qualify')}
                  disabled={challenging}
                  title="Narrow assertion scope with clarifying conditions"
                >
                  ✏️ Qualify
                </button>
                <button
                  type="button"
                  className={`pill-button ${selectedAction === 'rephrase' ? 'active' : ''}`}
                  onClick={() => setSelectedAction('rephrase')}
                  disabled={challenging}
                  title="Reword assertion without altering meaning"
                >
                  🔄 Rephrase
                </button>
                <button
                  type="button"
                  className={`pill-button ${selectedAction === 'remove' ? 'active' : ''}`}
                  onClick={() => setSelectedAction('remove')}
                  disabled={challenging}
                  title="Surgically remove sentence and unlink claim"
                >
                  🗑️ Remove
                </button>
              </div>

              {/* Action Description */}
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                {selectedAction === 'investigate' && 'Executes targeted research if research tools are active, or rechecks existing catalog.'}
                {selectedAction === 'qualify' && 'Revises the narration passage to add nuance or narrower scope.'}
                {selectedAction === 'rephrase' && 'Rewords narration to avoid ambiguous phrasing while preserving factual intent.'}
                {selectedAction === 'remove' && 'Surgically deletes the claim sentence from narration and marks claim unlinked.'}
              </div>

              {/* Optional Guidance Textarea */}
              {selectedAction !== 'remove' && (
                <div style={{ marginBottom: '0.85rem' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>
                    Challenge Directives / Instructions (Optional)
                  </label>
                  <textarea
                    placeholder={`e.g. "Focus on cryogenic temperatures below 20 mK" or "Clarify that classical computers use bits"`}
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    disabled={challenging}
                    rows={2}
                    style={{ width: '100%', fontSize: '0.85rem' }}
                  />
                </div>
              )}

              {challengeError && (
                <div className="banner banner-error" style={{ marginBottom: '0.75rem', fontSize: '0.8rem' }}>
                  <span>⚠️ {challengeError}</span>
                </div>
              )}

              {/* Challenge Result Summary If Done */}
              {challengeResult && (
                <div
                  style={{
                    padding: '0.75rem 1rem',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    border: '1px solid rgba(16, 185, 129, 0.3)',
                    borderRadius: 'var(--radius-sm)',
                    marginBottom: '0.85rem',
                    fontSize: '0.8rem',
                  }}
                >
                  <div style={{ fontWeight: 600, color: '#34d399', marginBottom: '0.25rem' }}>
                    ✓ Challenge Completed: {challengeResult.action.toUpperCase()} ({challengeResult.outcome})
                  </div>
                  <div style={{ color: 'var(--text-primary)', marginBottom: '0.3rem' }}>
                    {challengeResult.explanation}
                  </div>
                  {challengeResult.investigation_note && (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Note: {challengeResult.investigation_note}
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={challenging || claim.link_status === 'unlinked'}
                >
                  {challenging ? 'Executing Challenge...' : `Submit Challenge (${selectedAction})`}
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={challenging}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
