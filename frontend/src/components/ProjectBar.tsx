import React, { useState } from 'react';
import { ProjectMetadata } from '../types';

interface ProjectBarProps {
  projects: ProjectMetadata[];
  currentProjectId: string | null;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (title: string) => Promise<void>;
  loading: boolean;
  apiOnline: boolean;
}

export const ProjectBar: React.FC<ProjectBarProps> = ({
  projects,
  currentProjectId,
  onSelectProject,
  onCreateProject,
  loading,
  apiOnline,
}) => {
  const [showNewModal, setShowNewModal] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      setSubmitting(true);
      await onCreateProject(newTitle.trim());
      setNewTitle('');
      setShowNewModal(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-icon">🎬</span>
        <div>
          <div className="brand-title">AI Animation Studio</div>
          <div className="brand-subtitle">Script Builder • Local Studio</div>
        </div>
      </div>

      <div className="header-controls">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <label htmlFor="project-select" style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Project:
          </label>
          <select
            id="project-select"
            value={currentProjectId || ''}
            onChange={(e) => onSelectProject(e.target.value)}
            disabled={loading || projects.length === 0}
            style={{ minWidth: '220px' }}
          >
            {projects.length === 0 ? (
              <option value="">No projects available</option>
            ) : (
              projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.title} ({p.project_id.slice(0, 8)})
                </option>
              ))
            )}
          </select>

          <button
            className="btn-secondary"
            onClick={() => setShowNewModal(true)}
            disabled={loading}
            title="Create a new animation project"
          >
            <span>+</span> New Project
          </button>
        </div>

        <div className={`status-badge ${apiOnline ? '' : 'status-badge-offline'}`}>
          <span className={`status-dot ${apiOnline ? '' : 'status-dot-offline'}`}></span>
          <span>{apiOnline ? 'API Online (127.0.0.1:8000)' : 'API Offline (Disconnected)'}</span>
        </div>
      </div>

      {showNewModal && (
        <div className="modal-overlay" onClick={() => !submitting && setShowNewModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <form onSubmit={handleSubmit}>
              <div className="modal-header">
                <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Create New Animation Project</h3>
                <button
                  type="button"
                  style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.2rem' }}
                  onClick={() => setShowNewModal(false)}
                  disabled={submitting}
                >
                  ✕
                </button>
              </div>
              <div className="modal-body">
                <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
                  Project Title
                </label>
                <input
                  type="text"
                  placeholder="e.g. Inside a Black Hole Explainer"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  autoFocus
                  required
                  style={{ width: '100%', marginBottom: '1rem' }}
                  disabled={submitting}
                />
                <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  A dedicated project directory will be created under <code>backend/projects/</code> with an isolated
                  artifacts store and script state.
                </p>
              </div>
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowNewModal(false)}
                  disabled={submitting}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={submitting || !newTitle.trim()}>
                  {submitting ? 'Creating...' : 'Create Project'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </header>
  );
};
