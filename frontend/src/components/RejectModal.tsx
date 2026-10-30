import React, { useState } from 'react';
import { Script } from '../types';

interface RejectModalProps {
  script: Script;
  onConfirm: (reason?: string) => Promise<void>;
  onCancel: () => void;
  submitting: boolean;
}

export const RejectModal: React.FC<RejectModalProps> = ({
  script,
  onConfirm,
  onCancel,
  submitting,
}) => {
  const [reason, setReason] = useState('');

  const handleConfirm = () => {
    onConfirm(reason.trim() || undefined);
  };

  return (
    <div className="modal-overlay" onClick={() => !submitting && onCancel()}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fb7185' }}>
            Reject Draft v{script.version}
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
          <p style={{ fontSize: '0.875rem', marginBottom: '0.75rem' }}>
            Marking this draft as rejected prevents accidental finalization. You can provide an optional reason
            to guide the next revision.
          </p>

          <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Rejection Reason (optional):
          </label>
          <textarea
            placeholder="e.g. The pacing in the climax is too fast, or the explanation needs more visual metaphors."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            style={{ width: '100%', resize: 'vertical' }}
            disabled={submitting}
            autoFocus
          />
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
            className="btn-danger"
            onClick={handleConfirm}
            disabled={submitting}
          >
            {submitting ? 'Rejecting...' : 'Reject Draft'}
          </button>
        </div>
      </div>
    </div>
  );
};
