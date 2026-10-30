import React from 'react';
import { Script } from '../types';

interface FinalizeModalProps {
  script: Script;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
  submitting: boolean;
}

export const FinalizeModal: React.FC<FinalizeModalProps> = ({
  script,
  onConfirm,
  onCancel,
  submitting,
}) => {
  return (
    <div className="modal-overlay" onClick={() => !submitting && onCancel()}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#c084fc' }}>
            🔒 Finalize Script (Version {script.version})
          </h3>
          <button
            type="button"
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.2rem' }}
            onClick={onCancel}
            disabled={submitting}
          >
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div style={{ marginBottom: '1rem' }}>
            <p style={{ fontWeight: 600, fontSize: '0.95rem', marginBottom: '0.5rem' }}>
              Are you sure you want to finalize this script version?
            </p>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Finalization will:
            </p>
            <ul style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginLeft: '1.25rem', marginTop: '0.5rem', lineHeight: 1.6 }}>
              <li>Write an immutable artifact to <code>script/finalized/v{String(script.version).padStart(3, '0')}.json</code>.</li>
              <li>Establish this version as the official source of truth for downstream Scene Planning and Voiceover.</li>
              <li>Update project stage status for <code>script</code> to <code>completed</code>.</li>
              <li>Future revisions can still be made in the draft workspace without altering this finalized version.</li>
            </ul>
          </div>

          <div style={{ padding: '0.75rem', backgroundColor: 'var(--bg-card)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)', fontSize: '0.8rem' }}>
            <div><strong>Title:</strong> {script.title}</div>
            <div><strong>Sections:</strong> {script.sections.length} scene blocks</div>
            <div><strong>Target Duration:</strong> {script.target_duration_minutes} minutes</div>
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-finalize"
            onClick={onConfirm}
            disabled={submitting}
          >
            {submitting ? 'Finalizing...' : 'Confirm & Finalize Version'}
          </button>
        </div>
      </div>
    </div>
  );
};
