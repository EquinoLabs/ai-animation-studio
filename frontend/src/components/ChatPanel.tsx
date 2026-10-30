import React, { useState, useRef, useEffect } from 'react';
import { ConversationEvent, Script } from '../types';

interface ChatPanelProps {
  events: ConversationEvent[];
  script: Script | null;
  loading: boolean;
  onSendRevision: (instruction: string) => Promise<void>;
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
}) => {
  const [instruction, setInstruction] = useState('');
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events, loading]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim() || loading || !script) return;
    onSendRevision(instruction.trim());
    setInstruction('');
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

            let bubbleBg = 'var(--bg-card)';
            let bubbleBorder = 'var(--border-color)';
            let title = 'System Event';
            let icon = '🤖';

            if (isUser) {
              bubbleBg = 'rgba(99, 102, 241, 0.15)';
              bubbleBorder = 'rgba(99, 102, 241, 0.35)';
              title = isRevision ? 'Revision Instruction' : 'User Prompt';
              icon = '👤';
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

      {/* Suggestion Chips */}
      {script && (
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

      {/* Input Form */}
      <form
        onSubmit={handleSubmit}
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
    </div>
  );
};
