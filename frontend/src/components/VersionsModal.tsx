import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { Script } from '../types';

interface VersionsModalProps {
  projectId: string;
  onClose: () => void;
}

export const VersionsModal: React.FC<VersionsModalProps> = ({ projectId, onClose }) => {
  const [tab, setTab] = useState<'drafts' | 'finalized'>('drafts');
  const [drafts, setDrafts] = useState<Script[]>([]);
  const [finalized, setFinalized] = useState<Script[]>([]);
  const [selectedScript, setSelectedScript] = useState<Script | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    async function loadData() {
      try {
        setLoading(true);
        setError(null);
        const [dList, fList] = await Promise.all([
          api.getScriptVersions(projectId),
          api.getFinalizedVersions(projectId),
        ]);
        if (mounted) {
          setDrafts(dList);
          setFinalized(fList);
          if (dList.length > 0) {
            setSelectedScript(dList[dList.length - 1]);
          }
        }
      } catch (err: unknown) {
        if (mounted) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }
    loadData();
    return () => {
      mounted = false;
    };
  }, [projectId]);

  const activeList = tab === 'drafts' ? drafts : finalized;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card"
        style={{ maxWidth: '900px', height: '80vh', display: 'flex', flexDirection: 'column' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Script Versions & Artifacts</h3>
            <div style={{ display: 'flex', gap: '0.25rem', backgroundColor: 'var(--bg-primary)', padding: '0.2rem', borderRadius: 'var(--radius-sm)' }}>
              <button
                type="button"
                className={tab === 'drafts' ? 'btn-primary' : 'btn-secondary'}
                style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem' }}
                onClick={() => {
                  setTab('drafts');
                  if (drafts.length > 0) setSelectedScript(drafts[drafts.length - 1]);
                }}
              >
                Drafts ({drafts.length})
              </button>
              <button
                type="button"
                className={tab === 'finalized' ? 'btn-primary' : 'btn-secondary'}
                style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem' }}
                onClick={() => {
                  setTab('finalized');
                  if (finalized.length > 0) setSelectedScript(finalized[finalized.length - 1]);
                }}
              >
                Finalized ({finalized.length})
              </button>
            </div>
          </div>
          <button
            type="button"
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.2rem' }}
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* Version list column */}
          <div
            style={{
              width: '240px',
              borderRight: '1px solid var(--border-color)',
              overflowY: 'auto',
              padding: '0.75rem',
              backgroundColor: 'var(--bg-primary)',
            }}
          >
            {loading && <div style={{ color: 'var(--text-secondary)', padding: '1rem', fontSize: '0.85rem' }}>Loading versions...</div>}
            {error && <div style={{ color: 'var(--accent-rose)', padding: '1rem', fontSize: '0.85rem' }}>{error}</div>}
            {!loading && activeList.length === 0 && (
              <div style={{ color: 'var(--text-muted)', padding: '1rem', fontSize: '0.85rem' }}>
                No {tab} found.
              </div>
            )}
            {activeList.map((s) => {
              const isSelected = selectedScript?.version === s.version && (selectedScript?.status === s.status || tab === 'finalized');
              return (
                <div
                  key={`${s.version}-${s.status}`}
                  onClick={() => setSelectedScript(s)}
                  style={{
                    padding: '0.65rem 0.75rem',
                    marginBottom: '0.5rem',
                    borderRadius: 'var(--radius-sm)',
                    cursor: 'pointer',
                    backgroundColor: isSelected ? 'var(--bg-tertiary)' : 'var(--bg-card)',
                    border: isSelected ? '1px solid var(--accent-indigo)' : '1px solid var(--border-color)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                      {tab === 'finalized' ? `v${String(s.version).padStart(3, '0')}` : `Draft v${s.version}`}
                    </span>
                    <span className={`pill pill-${s.status}`} style={{ fontSize: '0.65rem' }}>
                      {s.status}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    {new Date(s.updated_at || s.created_at).toLocaleTimeString()}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Script preview column */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem', backgroundColor: 'var(--bg-secondary)' }}>
            {selectedScript ? (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <div>
                    <h4 style={{ fontSize: '1.1rem', fontWeight: 600 }}>{selectedScript.title}</h4>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      Version {selectedScript.version} • {selectedScript.target_duration_minutes} min duration •{' '}
                      {selectedScript.sections.length} sections
                    </p>
                  </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {selectedScript.sections.map((sec, idx) => (
                    <div
                      key={sec.id || idx}
                      style={{
                        padding: '0.75rem 1rem',
                        backgroundColor: 'var(--bg-card)',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
                        <span
                          style={{
                            fontSize: '0.65rem',
                            fontWeight: 700,
                            textTransform: 'uppercase',
                            padding: '0.15rem 0.4rem',
                            borderRadius: '4px',
                            backgroundColor: 'var(--bg-tertiary)',
                            color: 'var(--accent-blue)',
                          }}
                        >
                          {sec.type}
                        </span>
                        <strong style={{ fontSize: '0.85rem' }}>{sec.heading}</strong>
                      </div>
                      <p style={{ fontSize: '0.85rem', color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
                        {sec.narration}
                      </p>
                      {sec.visual_notes && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                          🎥 {sec.visual_notes}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div style={{ color: 'var(--text-muted)', textAlign: 'center', marginTop: '2rem' }}>
                Select a version on the left to inspect its content.
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
