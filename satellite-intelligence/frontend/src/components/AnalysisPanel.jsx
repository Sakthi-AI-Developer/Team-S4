const ACTIONS = [
  ['ndvi', 'Analyze NDVI', 'runNDVI'],
  ['ndwi', 'Analyze NDWI', 'runNDWI'],
  ['ndbi', 'Analyze NDBI', 'runNDBI'],
  ['landcover', 'Analyze Land Use', 'runLandcover'],
  ['change_detection', 'Analyze Change', 'runChangeDetection'],
];

export default function AnalysisPanel({ busy, onRun, onRunAll, datasetAvailable }) {
  return (
    <section className="analysis-panel panel" aria-label="Analysis controls">
      <div className="panel-header">
        <div><p className="eyebrow">PROCESSING</p><h2>Run an analysis</h2></div>
        <span className="processing-note">Local rasters · no live API</span>
      </div>
      <div className="analysis-actions">
        {ACTIONS.map(([key, label]) => (
          <button className="secondary-button" type="button" key={key} disabled={Boolean(busy) || !datasetAvailable} onClick={() => onRun(key)}>
            {busy === key ? <><span className="spinner" /> Running</> : label}
          </button>
        ))}
        <button className="primary-button" type="button" disabled={Boolean(busy) || !datasetAvailable} onClick={onRunAll}>
          {busy === 'all' ? <><span className="spinner" /> Processing imagery</> : '✦ Run complete analysis'}
        </button>
      </div>
    </section>
  );
}
