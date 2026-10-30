import React, { useEffect, useState } from 'react';
import { api, ApiError } from '../api';

interface ResearchBriefModalProps {
  projectId: string;
  runId?: string | null;
  onClose: () => void;
}

export const ResearchBriefModal: React.FC<ResearchBriefModalProps> = ({
  projectId,
  runId,
  onClose,
}) => {
  const [briefMarkdown, setBriefMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    let active = true;
    const fetchBrief = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.getResearchBrief(projectId, runId || undefined);
        if (active) {
          setBriefMarkdown(res.brief_markdown);
        }
      } catch (err) {
        if (active) {
          if (err instanceof ApiError && err.status === 404) {
            setError('No evidence brief artifact was generated for this research run.');
          } else {
            setError(err instanceof ApiError ? err.detail : 'Failed to load research brief.');
          }
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchBrief();
    return () => {
      active = false;
    };
  }, [projectId, runId]);

  const handleCopy = () => {
    if (briefMarkdown) {
      navigator.clipboard.writeText(briefMarkdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card modal-lg"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '850px', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
      >
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <span style={{ fontSize: '1.4rem' }}>📑</span>
            <div>
              <h3 style={{ fontSize: '1.15rem', fontWeight: 600 }}>Research Evidence Brief</h3>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Authoritative synthesis compiled from verified source extractions.
                {runId && <span style={{ marginLeft: '0.5rem', fontFamily: 'monospace' }}>({runId})</span>}
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

        <div className="modal-body" style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
          {loading && (
            <div style={{ textAlign: 'center', padding: '3rem 0', color: 'var(--text-secondary)' }}>
              <div className="spinner" style={{ margin: '0 auto 1rem' }} />
              <p>Loading evidence brief artifact...</p>
            </div>
          )}

          {error && !loading && (
            <div className="banner banner-error" style={{ margin: '1rem 0' }}>
              <span>⚠️ {error}</span>
            </div>
          )}

          {!loading && !error && briefMarkdown && (
            <div className="markdown-brief-viewer">
              <pre
                style={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'inherit',
                  fontSize: '0.9rem',
                  lineHeight: '1.6',
                  color: 'var(--text-primary)',
                  backgroundColor: 'var(--bg-primary)',
                  padding: '1.25rem',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                }}
              >
                {briefMarkdown}
              </pre>
            </div>
          )}
        </div>

        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleCopy}
            disabled={!briefMarkdown || loading}
          >
            {copied ? '✓ Copied to Clipboard' : '📋 Copy Markdown'}
          </button>
          <button type="button" className="btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
