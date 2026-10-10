import { useState } from 'react';
import { downloadArtifact } from '../services/api';

export default function DownloadPanel({ resultId, artifacts, loading, error, onError }) {
  const [downloading, setDownloading] = useState('');

  async function handleDownload(artifact) {
    if (!resultId || downloading) return;
    setDownloading(artifact.artifact_name);
    try {
      const response = await downloadArtifact(resultId, artifact.artifact_name);
      const blobUrl = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = artifact.artifact_name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      const detail = error.response?.data?.detail;
      onError(typeof detail === 'string' ? detail : `Unable to download ${artifact.artifact_name}.`);
    } finally {
      setDownloading('');
    }
  }

  return (
    <div className="download-panel-content">
      {loading && <p className="panel-note" role="status">Loading saved artifact metadata…</p>}
      {error && <p className="panel-note" role="alert">{error}</p>}
      {!loading && !error && !artifacts.length && (
        <p className="panel-note">{resultId ? 'No persisted artifacts are available for this result.' : 'Run or load an analysis to see its saved files.'}</p>
      )}
      <div className="download-list" aria-label="Saved artifacts">
        {artifacts.map((artifact) => {
          const isDownloading = downloading === artifact.artifact_name;
          return (
            <button
              className="download-row"
              type="button"
              key={artifact.artifact_name}
              disabled={!resultId || Boolean(downloading)}
              onClick={() => handleDownload(artifact)}
            >
              <span>{isDownloading ? '…' : '⇩'}</span>
              <b>{isDownloading ? 'Downloading' : artifact.artifact_name}</b>
              <small>{artifact.artifact_type} · {artifact.size_bytes.toLocaleString()} bytes</small>
            </button>
          );
        })}
      </div>
    </div>
  );
}
