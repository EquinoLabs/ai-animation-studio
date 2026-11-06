import React, { useState, useRef, useEffect } from 'react';
import { ConversationEvent, Script, ChallengeAction, ScriptFactCheckReport } from '../types';

interface ChatPanelProps {
  events: ConversationEvent[];
  script: Script | null;
  loading: boolean;
  onSendRevision: (instruction: string) => Promise<void>;
  onChallengeClaim?: (claimId: string, action: ChallengeAction, instruction?: string) => Promise<void>;
  factCheckReport?: ScriptFactCheckReport | null;
}

const SUGGESTIONS = [
  'Make the hook more dramatic and punchy',
  'Add visual anime cues for dynamic character reactions',
  'Simplify the technical explanation with an everyday analogy',
  'Slow down the pacing in the climax',
  'Strengthen the call to action / takeaway',
];

export const ChatPanel: React.FC<ChatPanelProps> = ({
  events,
  script,
  loading,
  onSendRevision,
  onChallengeClaim,
  factCheckReport,
}) => {
  const [mode, setMode] = useState<'revision' | 'challenge'>('revision');
  const [instruction, setInstruction] = useState('');
  const [targetClaimId, setTargetClaimId] = useState<string>('');
  const [challengeAction, setChallengeAction] = useState<ChallengeAction>('investigate');
  const [challengeInstruction, setChallengeInstruction] = useState<string>('');
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Active claims from current script (omits unlinked claims)
  const activeClaims = (script?.claims || []).filter(
    (c) => c.link_status !== 'unlinked'
  );

  useEffect(() => {
    if (activeClaims.length > 0 && !targetClaimId) {
      setTargetClaimId(activeClaims[0].claim_id);
    }
  }, [activeClaims, targetClaimId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events, loading]);

  const handleRevisionSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim() || loading || !script) return;
    onSendRevision(instruction.trim());
    setInstruction('');
  };

  const handleChallengeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetClaimId || loading || !onChallengeClaim || !script) return;
    await onChallengeClaim(
      targetClaimId,
      challengeAction,
      challengeInstruction.trim() || undefined
    );
    setChallengeInstruction('');
  };

  const handleSuggestionClick = (sug: string) => {
    setInstruction(sug);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Panel Header */}
      <div
        style={{
          padding: '1rem 1.25rem',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        <div>
          <h3 style={{ fontSize: '0.95rem', fontWeight: 600 }}>Revision Assistant</h3>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
            Iterative conversational script refinement
          </p>
        </div>
        {script && (
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Draft v{script.version}
          </span>
        )}
      </div>

      {/* Events Timeline */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '1.25rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        }}
      >
        {events.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--text-muted)', margin: 'auto 0', fontSize: '0.85rem' }}>
            <div style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>💬</div>
            No conversation events yet.
            <br />
            Create or start a script to begin conversational drafting.
          </div>
        ) : (
          events.map((ev, index) => {
            const isUser = Boolean(ev.user_input && !ev.system_output);
            const isSystemNotice = !ev.user_input && !ev.system_output;
            const isRevision = ev.event_type.includes('revis');
            const isRejection = ev.event_type.includes('reject');
            const isAcceptance = ev.event_type.includes('accept');
            const isFinalization = ev.event_type.includes('finaliz');
            const isChallenge = ev.event_type === 'fact_challenge';

            let bubbleBg = 'var(--bg-card)';
            let bubbleBorder = 'var(--border-color)';
            let title = 'System Event';
            let icon = '🤖';

            if (isUser) {
              bubbleBg = 'rgba(99, 102, 241, 0.15)';
              bubbleBorder = 'rgba(99, 102, 241, 0.35)';
              title = isRevision ? 'Revision Instruction' : 'User Prompt';
              icon = '👤';
            } else if (isChallenge) {
              bubbleBg = 'rgba(168, 85, 247, 0.12)';
              bubbleBorder = 'rgba(168, 85, 247, 0.4)';
              title = 'Conversational Fact Challenge';
              icon = '⚖️';
            } else if (isRejection) {
              bubbleBg = 'rgba(244, 63, 94, 0.12)';
              bubbleBorder = 'rgba(244, 63, 94, 0.3)';
              title = 'Draft Rejected';
              icon = '✕';
            } else if (isAcceptance) {
              bubbleBg = 'rgba(16, 185, 129, 0.12)';
              bubbleBorder = 'rgba(16, 185, 129, 0.3)';
              title = 'Draft Accepted';
              icon = '✓';
            } else if (isFinalization) {
              bubbleBg = 'rgba(168, 85, 247, 0.15)';
              bubbleBorder = 'rgba(168, 85, 247, 0.35)';
              title = 'Script Finalized';
              icon = '🔒';
            } else if (ev.event_type === 'draft_generated' || ev.event_type === 'draft_revised') {
              title = ev.event_type === 'draft_generated' ? 'Initial Draft Generated' : 'Revised Draft Generated';
              icon = '✨';
            }

            const challengeMeta = isChallenge ? (ev.metadata as Record<string, any> | undefined) : undefined;

            return (
              <div
                key={`${ev.timestamp}-${index}`}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignSelf: isUser ? 'flex-end' : 'flex-start',
                  maxWidth: '92%',
                }}
              >
                <div
                  style={{
                    backgroundColor: bubbleBg,
                    border: `1px solid ${bubbleBorder}`,
                    borderRadius: 'var(--radius-md)',
                    padding: '0.75rem 1rem',
                    fontSize: '0.85rem',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: '0.35rem',
                      fontSize: '0.75rem',
                      color: 'var(--text-secondary)',
                      gap: '0.5rem',
                    }}
                  >
                    <span>
                      {icon} <strong>{title}</strong>
                    </span>
                    <span>{new Date(ev.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                  </div>

                  {ev.user_input && (
                    <div style={{ whiteSpace: 'pre-wrap', color: 'var(--text-primary)' }}>{ev.user_input}</div>
                  )}

                  {ev.system_output && (
                    <div style={{ whiteSpace: 'pre-wrap', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                      {ev.system_output}
                    </div>
                  )}

                  {isSystemNotice && (
                    <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                      Event: {ev.event_type}
                    </div>
                  )}

                  {isChallenge && challengeMeta && (
                    <div style={{ marginTop: '0.4rem', display: 'flex', flexDirection: 'column', gap: '0.35rem', fontSize: '0.8rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                        <span className="badge" style={{ textTransform: 'uppercase', backgroundColor: 'rgba(168, 85, 247, 0.2)', color: '#c084fc', border: '1px solid rgba(168, 85, 247, 0.4)' }}>
                          Action: {challengeMeta.action}
                        </span>
                        <span style={{ fontFamily: 'monospace', color: 'var(--accent-blue)', fontWeight: 600 }}>
                          [{challengeMeta.target_claim_id}]
                        </span>
                        {challengeMeta.new_version && (
                          <span className="badge badge-secondary" style={{ fontSize: '0.7rem' }}>
                            Draft v{challengeMeta.new_version}
                          </span>
                        )}
                      </div>

                      {challengeMeta.target_claim_text && (
                        <div style={{ color: 'var(--text-primary)', fontStyle: 'italic', fontSize: '0.78rem' }}>
                          “{challengeMeta.target_claim_text}”
                        </div>
                      )}

                      {/* Fresh Research vs Recheck Honest Indicator */}
                      <div style={{ marginTop: '0.2rem' }}>
                        {challengeMeta.investigation_performed ? (
                          <span className="badge badge-success" style={{ fontSize: '0.72rem' }}>
                            ✓ Fresh Research Run Executed ({challengeMeta.new_run_id})
                          </span>
                        ) : challengeMeta.action === 'investigate' ? (
                          <span className="badge badge-warning" style={{ fontSize: '0.72rem' }}>
                            ℹ️ Existing Evidence Recheck (No fresh research performed)
                          </span>
                        ) : null}
                      </div>

                      {challengeMeta.investigation_note && (
                        <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>
                          {challengeMeta.investigation_note}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {loading && (
          <div style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
            <span>⏳</span> Generating revised draft with local LLM...
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* Suggestion Chips (Revision Mode Only) */}
      {script && mode === 'revision' && (
        <div
          style={{
            padding: '0.5rem 1rem',
            borderTop: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-primary)',
            overflowX: 'auto',
            whiteSpace: 'nowrap',
            display: 'flex',
            gap: '0.4rem',
          }}
        >
          {SUGGESTIONS.map((sug, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleSuggestionClick(sug)}
              disabled={loading}
              style={{
                fontSize: '0.72rem',
                padding: '0.2rem 0.5rem',
                backgroundColor: 'var(--bg-card)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: '9999px',
                cursor: 'pointer',
              }}
            >
              + {sug}
            </button>
          ))}
        </div>
      )}

      {/* Mode Switcher Bar */}
      <div style={{ display: 'flex', borderTop: '1px solid var(--border-color)', backgroundColor: 'var(--bg-primary)' }}>
        <button
          type="button"
          className={`workspace-tab-btn ${mode === 'revision' ? 'active' : ''}`}
          onClick={() => setMode('revision')}
          style={{ flex: 1, justifyContent: 'center', borderRadius: 0, padding: '0.45rem', fontSize: '0.8rem' }}
        >
          📝 General Revision
        </button>
        <button
          type="button"
          className={`workspace-tab-btn ${mode === 'challenge' ? 'active' : ''}`}
          onClick={() => setMode('challenge')}
          disabled={!script || activeClaims.length === 0}
          style={{ flex: 1, justifyContent: 'center', borderRadius: 0, padding: '0.45rem', fontSize: '0.8rem' }}
        >
          ⚖️ Challenge Claim ({activeClaims.length})
        </button>
      </div>

      {/* Input Form: Revision Mode */}
      {mode === 'revision' ? (
        <form
          onSubmit={handleRevisionSubmit}
          style={{
            padding: '1rem',
            borderTop: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <textarea
              placeholder={
                script
                  ? 'Enter revision instructions (e.g. "Make the middle section shorter and add a metaphor about black holes")...'
                  : 'Start a script draft first to enable revisions.'
              }
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              disabled={loading || !script}
              rows={3}
              style={{ width: '100%', resize: 'none' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                Shift + Enter for new line
              </span>
              <button
                type="submit"
                className="btn-primary"
                disabled={loading || !script || !instruction.trim()}
                style={{ padding: '0.4rem 1rem' }}
              >
                {loading ? 'Revising...' : 'Send Revision'}
              </button>
            </div>
          </div>
        </form>
      ) : (
        /* Input Form: Claim Challenge Mode */
        <form
          onSubmit={handleChallengeSubmit}
          style={{
            padding: '1rem',
            borderTop: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
                Target Factual Claim
              </label>
              <select
                value={targetClaimId}
                onChange={(e) => setTargetClaimId(e.target.value)}
                disabled={loading || activeClaims.length === 0}
                style={{ width: '100%', fontSize: '0.8rem', padding: '0.4rem 0.5rem' }}
              >
                {activeClaims.map((c) => {
                  const assessment = factCheckReport?.assessments.find((a) => a.claim_id === c.claim_id);
                  const st = assessment?.status || c.assessment_status || 'not_checked';
                  return (
                    <option key={c.claim_id} value={c.claim_id}>
                      [{c.claim_id}] {c.claim_text.length > 45 ? `${c.claim_text.slice(0, 45)}...` : c.claim_text} ({st})
                    </option>
                  );
                })}
              </select>
            </div>

            {/* Action Selection Buttons */}
            <div>
              <label style={{ display: 'block', fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
                Challenge Action
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.35rem' }}>
                <button
                  type="button"
                  className={`pill-button ${challengeAction === 'investigate' ? 'active' : ''}`}
                  onClick={() => setChallengeAction('investigate')}
                  disabled={loading}
                  style={{ fontSize: '0.75rem', padding: '0.3rem 0.4rem' }}
                >
                  Investigate
                </button>
                <button
                  type="button"
                  className={`pill-button ${challengeAction === 'qualify' ? 'active' : ''}`}
                  onClick={() => setChallengeAction('qualify')}
                  disabled={loading}
                  style={{ fontSize: '0.75rem', padding: '0.3rem 0.4rem' }}
                >
                  Qualify
                </button>
                <button
                  type="button"
                  className={`pill-button ${challengeAction === 'rephrase' ? 'active' : ''}`}
                  onClick={() => setChallengeAction('rephrase')}
                  disabled={loading}
                  style={{ fontSize: '0.75rem', padding: '0.3rem 0.4rem' }}
                >
                  Rephrase
                </button>
                <button
                  type="button"
                  className={`pill-button ${challengeAction === 'remove' ? 'active' : ''}`}
                  onClick={() => setChallengeAction('remove')}
                  disabled={loading}
                  style={{ fontSize: '0.75rem', padding: '0.3rem 0.4rem' }}
                >
                  Remove
                </button>
              </div>
            </div>

            {/* Optional Guidance */}
            {challengeAction !== 'remove' && (
              <textarea
                placeholder="Optional challenge directives (e.g. 'Focus on cryogenic temperatures')..."
                value={challengeInstruction}
                onChange={(e) => setChallengeInstruction(e.target.value)}
                disabled={loading}
                rows={2}
                style={{ width: '100%', resize: 'none', fontSize: '0.8rem' }}
              />
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="submit"
                className="btn-primary"
                disabled={loading || !targetClaimId}
                style={{ padding: '0.4rem 1rem', fontSize: '0.8rem' }}
              >
                {loading ? 'Processing...' : `Submit Challenge (${challengeAction})`}
              </button>
            </div>
          </div>
        </form>
      )}
    </div>
  );
};
