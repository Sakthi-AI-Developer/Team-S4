import { useMemo, useRef, useState } from 'react';
import { downloadArtifact, getAnalysisRecord, getResultArtifacts } from '../services/api';
import { createReportData, interpretationFor, reportToCsv, reportToJson } from './analysisReport';

function formatDate(value) {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown date' : date.toLocaleString();
}

function displayValue(value) {
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value);
}

function saveTextFile(filename, mediaType, content) {
  const url = URL.createObjectURL(new Blob([content], { type: mediaType }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function AnalysisHistory({
  results = [],
  loading = false,
  error = '',
  hasMore = false,
  loadingMore = false,
  onRefresh,
  onLoadMore,
  onViewInGis,
}) {
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [artifactError, setArtifactError] = useState('');
  const [downloading, setDownloading] = useState('');
  const reportRequest = useRef(0);

  const types = useMemo(
    () => [...new Set(results.map((item) => item.analysis).filter(Boolean))].sort(),
    [results],
  );
  const filteredResults = useMemo(
    () => results.filter((item) => (
      (statusFilter === 'all' || (item.status ?? 'unknown') === statusFilter)
      && (typeFilter === 'all' || item.analysis === typeFilter)
    )),
    [results, statusFilter, typeFilter],
  );
  const selectedEntry = results.find((item) => item.id === selectedId);
  const report = selectedEntry ? createReportData(selectedEntry, detail ?? {}) : null;

  async function openReport(entry) {
    const requestId = reportRequest.current + 1;
    reportRequest.current = requestId;
    setSelectedId(entry.id);
    setDetail(null);
    setArtifacts([]);
    setDetailError('');
    setArtifactError('');
    setDetailLoading(true);
    const [detailResult, artifactResult] = await Promise.allSettled([
      getAnalysisRecord(entry.id),
      getResultArtifacts(entry.id),
    ]);
    if (requestId !== reportRequest.current) return;
    if (detailResult.status === 'fulfilled') {
      setDetail(detailResult.value);
    } else {
      const responseDetail = detailResult.reason?.response?.data?.detail;
      setDetailError(typeof responseDetail === 'string'
        ? responseDetail
        : 'Analysis details could not be loaded. Please retry.');
    }
    if (artifactResult.status === 'fulfilled') {
      setArtifacts(artifactResult.value.artifacts ?? []);
    } else {
      setArtifactError('Saved artifact metadata is unavailable. You can retry by reopening this report.');
    }
    setDetailLoading(false);
  }

  async function downloadReportArtifact(artifact) {
    if (!selectedEntry || downloading) return;
    setDownloading(artifact.artifact_name);
    setArtifactError('');
    try {
      const response = await downloadArtifact(selectedEntry.id, artifact.artifact_name);
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = artifact.artifact_name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      const responseDetail = requestError?.response?.data?.detail;
      setArtifactError(typeof responseDetail === 'string'
        ? responseDetail
        : `Unable to download ${artifact.artifact_name}.`);
    } finally {
      setDownloading('');
    }
  }

  function exportCsv() {
    if (!report) return;
    saveTextFile(`analysis-${report.id}.csv`, 'text/csv;charset=utf-8', reportToCsv(report));
  }

  function exportJson() {
    if (!report) return;
    saveTextFile(
      `analysis-${report.id}.json`,
      'application/json;charset=utf-8',
      reportToJson(report),
    );
  }

  return (
    <section className="panel analysis-history" aria-label="Analysis history">
      <div className="panel-header">
        <div><p className="eyebrow">ANALYSIS RECORDS</p><h2>History and reports</h2></div>
        <button className="secondary-button" type="button" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh history'}
        </button>
      </div>
      <p className="panel-note">
        Status and metadata are shown as stored. Missing dates or statistics are not inferred.
      </p>
      <div className="history-filters">
        <label>
          Status
          <select aria-label="Filter analysis history by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            {[...new Set(results.map((item) => item.status ?? 'unknown'))].sort().map((status) => (
              <option value={status} key={status}>{status}</option>
            ))}
          </select>
        </label>
        <label>
          Analysis type
          <select aria-label="Filter analysis history by analysis type" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">All types</option>
            {types.map((type) => <option value={type} key={type}>{type}</option>)}
          </select>
        </label>
      </div>
      {loading && !results.length && <p className="history-state" role="status">Loading analysis history…</p>}
      {error && (
        <div className="history-state history-error" role="alert">
          <span>{error}</span>
          <button className="secondary-button" type="button" onClick={onRefresh}>Retry</button>
        </div>
      )}
      {!loading && !error && results.length === 0 && (
        <p className="history-state">No saved analyses are available yet.</p>
      )}
      {!loading && !error && results.length > 0 && filteredResults.length === 0 && (
        <p className="history-state">No history entries match the selected filters.</p>
      )}
      {filteredResults.length > 0 && (
        <div className="history-list" aria-label="Analysis records">
          {filteredResults.map((entry) => {
            const source = entry.history?.source_scenes?.[0];
            return (
              <article className="history-entry" key={entry.id}>
                <div className="history-entry-main">
                  <strong>{entry.analysis || 'Analysis'}</strong>
                  <span className={`history-status status-${entry.status ?? 'unknown'}`}>
                    {entry.status ?? 'Status unknown'}
                  </span>
                  <small>Created: {formatDate(entry.created_at)}</small>
                  {source && (
                    <small>
                      {source.role ?? 'Source'}: {source.filename ?? source.id ?? 'Unknown scene'}
                      {' · '}Acquired: {source.acquisition_date ?? 'Unknown'}
                    </small>
                  )}
                  <code>{entry.id}</code>
                </div>
                <button className="secondary-button" type="button" onClick={() => openReport(entry)}>
                  Open report
                </button>
              </article>
            );
          })}
        </div>
      )}
      {hasMore && (
        <button
          className="secondary-button history-more"
          type="button"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading more…' : 'Load more history'}
        </button>
      )}

      {selectedEntry && (
        <section className="analysis-report" aria-label="Analysis report" aria-live="polite">
          <div className="panel-header report-heading">
            <div>
              <p className="eyebrow">ANALYSIS REPORT</p>
              <h3>{report.analysis}</h3>
              <small>Record {report.id}</small>
            </div>
            <button
              className="text-button"
              type="button"
              onClick={() => {
                reportRequest.current += 1;
                setSelectedId('');
              }}
            >
              Close report
            </button>
          </div>
          {detailLoading && <p className="history-state" role="status">Loading report details and artifact list…</p>}
          {detailError && (
            <div className="history-state history-error" role="alert">
              <span>{detailError}</span>
              <button className="secondary-button" type="button" onClick={() => openReport(selectedEntry)}>Retry</button>
            </div>
          )}
          {!detailLoading && !detailError && (
            <>
              <dl className="report-metadata">
                <div><dt>Status</dt><dd>{report.status}</dd></div>
                <div><dt>Created</dt><dd>{formatDate(report.created_at)}</dd></div>
                <div><dt>Completed</dt><dd>{formatDate(report.completed_at)}</dd></div>
                <div><dt>Method</dt><dd>{report.method ?? 'Not recorded'}</dd></div>
                <div><dt>Threshold</dt><dd>{report.threshold ?? 'Not recorded'} {report.threshold_units ?? ''}</dd></div>
              </dl>
              <h4>Source scenes and provenance</h4>
              {report.source_scenes.length ? (
                <ul className="report-list">
                  {report.source_scenes.map((scene, index) => (
                    <li key={`${scene.role ?? 'scene'}-${scene.id ?? index}`}>
                      <strong>{scene.role ?? 'Scene'}:</strong> {scene.filename ?? scene.id ?? 'Scene identifier unavailable'}
                      {' · '}Acquisition: {scene.acquisition_date ?? 'Unknown'}
                      {(scene.platform || scene.sensor) && ` · Platform/sensor: ${[scene.platform, scene.sensor].filter(Boolean).join(' / ')}`}
                      {scene.band_mapping && ` · Bands: ${displayValue(scene.band_mapping)}`}
                    </li>
                  ))}
                </ul>
              ) : <p className="panel-note">Scene identifiers and acquisition dates were not recorded.</p>}
              <h4>Parameters and spatial metadata</h4>
              <dl className="report-metadata">
                {Object.entries(report.parameters).map(([key, value]) => (
                  <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{displayValue(value)}</dd></div>
                ))}
                {Object.entries(report.spatial_metadata).map(([key, value]) => (
                  <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{displayValue(value)}</dd></div>
                ))}
                {!Object.keys(report.parameters).length && !Object.keys(report.spatial_metadata).length && (
                  <div><dt>Metadata</dt><dd>Not recorded</dd></div>
                )}
              </dl>
              <h4>Computed statistics and metrics</h4>
              {Object.keys(report.statistics).length || Object.keys(report.metrics).length ? (
                <dl className="report-metadata">
                  {Object.entries(report.statistics).flatMap(([group, values]) =>
                    Object.entries(values).map(([key, value]) => (
                      <div key={`${group}.${key}`}><dt>{`${group} · ${key.replaceAll('_', ' ')}`}</dt><dd>{displayValue(value)}</dd></div>
                    )),
                  )}
                  {Object.entries(report.metrics).map(([key, value]) => (
                    <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{displayValue(value)}</dd></div>
                  ))}
                </dl>
              ) : <p className="panel-note">No statistics were recorded for this analysis.</p>}
              <p className="report-interpretation">{interpretationFor(report)}</p>
              {(report.limitations || report.warnings.length > 0) && (
                <>
                  <h4>Recorded limitations and warnings</h4>
                  {report.limitations && <p className="panel-note">{report.limitations}</p>}
                  {report.warnings.length > 0 && (
                    <ul className="report-list">
                      {report.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
                    </ul>
                  )}
                </>
              )}
              <p className="panel-note">
                Thresholds and derived values can vary with sensor, season, geography, input quality, masking and preprocessing.
                Numerical outputs are not ground truth or causal explanations.
              </p>
              <h4>Available result files</h4>
              {artifactError && <p className="history-state history-error" role="alert">{artifactError}</p>}
              {!artifactError && !artifacts.length && !detailLoading && (
                <p className="panel-note">No registered result artifacts are available.</p>
              )}
              <div className="report-artifacts">
                {artifacts.map((artifact) => (
                  <button
                    className="secondary-button"
                    type="button"
                    key={artifact.artifact_name}
                    disabled={Boolean(downloading)}
                    onClick={() => downloadReportArtifact(artifact)}
                  >
                    {downloading === artifact.artifact_name ? 'Downloading…' : `Download ${artifact.artifact_name}`}
                    {' '}({artifact.size_bytes?.toLocaleString?.() ?? 'size unknown'} bytes)
                  </button>
                ))}
              </div>
              {selectedEntry.status === 'completed' && (
                <button className="secondary-button" type="button" onClick={() => onViewInGis?.(selectedEntry.id)}>
                  View completed result in GIS
                </button>
              )}
              <div className="report-actions" aria-label="Report export options">
                <button className="secondary-button" type="button" onClick={exportCsv}>Export summary CSV</button>
                <button className="secondary-button" type="button" onClick={exportJson}>Export summary JSON</button>
                <button className="secondary-button" type="button" onClick={() => window.print()}>Print report</button>
              </div>
            </>
          )}
        </section>
      )}
    </section>
  );
}
