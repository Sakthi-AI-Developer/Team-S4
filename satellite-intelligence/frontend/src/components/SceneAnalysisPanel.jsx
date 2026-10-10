import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import {
  analyzeImageryScene,
  downloadArtifact,
  getImageryAnalysis,
  getImageryAnalysisTypes,
} from '../services/api';

const MapView = lazy(() => import('./MapView'));

function requestErrorMessage(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
  if (error.code === 'ECONNABORTED') {
    return 'Scene analysis timed out. Check the saved analysis status before retrying.';
  }
  return 'Scene analysis could not be completed. The backend did not confirm a saved result.';
}

function automaticMapping(definition) {
  return Object.fromEntries(
    Object.entries(definition?.detected_bands ?? {})
      .filter(([, index]) => Number.isInteger(index))
      .map(([role, index]) => [role, String(index)]),
  );
}

export default function SceneAnalysisPanel({ sceneId, metadata, onAnalysisCompleted }) {
  const [analysisTypes, setAnalysisTypes] = useState([]);
  const [analysisType, setAnalysisType] = useState('');
  const [mapping, setMapping] = useState({});
  const [featureBands, setFeatureBands] = useState([]);
  const [clusterCount, setClusterCount] = useState(3);
  const [loadingTypes, setLoadingTypes] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setLoadingTypes(true);
    setAnalysis(null);
    setError('');
    getImageryAnalysisTypes(sceneId)
      .then((response) => {
        if (!active) return;
        const types = response.analysis_types ?? [];
        setAnalysisTypes(types);
        const preferred = types.find((item) => item.id === 'ndvi') ?? types[0];
        setAnalysisType(preferred?.id ?? '');
        setMapping(automaticMapping(preferred));
        const count = Number(metadata?.band_count ?? 0);
        setFeatureBands(Array.from({ length: Math.min(count, 3) }, (_, index) => index + 1));
      })
      .catch((requestError) => {
        if (active) setError(requestErrorMessage(requestError));
      })
      .finally(() => {
        if (active) setLoadingTypes(false);
      });
    return () => {
      active = false;
    };
  }, [sceneId, metadata?.band_count]);

  useEffect(() => {
    if (!analysis?.id || analysis.status !== 'processing') return undefined;
    let active = true;
    let timer;
    const poll = async () => {
      try {
        const response = await getImageryAnalysis(analysis.id);
        if (!active) return;
        setAnalysis(response);
        if (response.status === 'processing') {
          timer = setTimeout(poll, 2000);
        }
      } catch (requestError) {
        if (active) setError(requestErrorMessage(requestError));
      }
    };
    timer = setTimeout(poll, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [analysis]);

  useEffect(() => {
    if (analysis?.status === 'completed' && analysis.result?.preview?.artifact_name) {
      onAnalysisCompleted?.(analysis);
    }
  }, [analysis, onAnalysisCompleted]);

  const selectedType = useMemo(
    () => analysisTypes.find((item) => item.id === analysisType),
    [analysisTypes, analysisType],
  );
  const bands = metadata?.bands ?? [];
  const isIndex = analysisType === 'ndvi' || analysisType === 'ndwi';
  const mappingComplete = !isIndex
    || Object.keys(selectedType?.roles ?? {}).every((role) => Number.isInteger(Number(mapping[role])));
  const selectedFeatureBands = featureBands.map(Number);

  function changeType(value) {
    const next = analysisTypes.find((item) => item.id === value);
    setAnalysisType(value);
    setMapping(automaticMapping(next));
    setAnalysis(null);
    setError('');
  }

  function toggleFeatureBand(index) {
    setFeatureBands((current) => current.includes(index)
      ? current.filter((selected) => selected !== index)
      : [...current, index].sort((first, second) => first - second));
  }

  async function submitAnalysis(event) {
    event.preventDefault();
    if (!sceneId || !analysisType || submitting) return;
    setSubmitting(true);
    setError('');
    setAnalysis(null);
    const payload = {
      analysis_type: analysisType,
      ...(isIndex
        ? { band_mapping: Object.fromEntries(Object.entries(mapping).map(([role, index]) => [role, Number(index)])) }
        : {
          feature_bands: selectedFeatureBands,
          cluster_count: Number(clusterCount),
          random_seed: 42,
        }),
    };
    try {
      setAnalysis(await analyzeImageryScene(sceneId, payload));
    } catch (requestError) {
      setError(requestErrorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  async function downloadRaster() {
    const artifactName = analysis?.result?.raster?.artifact_name;
    if (!analysis?.id || !artifactName || downloading) return;
    setDownloading(true);
    setError('');
    try {
      const response = await downloadArtifact(analysis.id, artifactName);
      const objectUrl = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = artifactName;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (requestError) {
      setError(requestErrorMessage(requestError));
    } finally {
      setDownloading(false);
    }
  }

  const result = analysis?.result;
  const statistics = result?.statistics;
  const canSubmit = !loadingTypes
    && !submitting
    && Boolean(selectedType)
    && mappingComplete
    && (analysisType !== 'kmeans' || (selectedFeatureBands.length >= 2 && selectedFeatureBands.length <= 16));

  return (
    <section className="scene-analysis" aria-label="Satellite scene analysis">
      <div className="scene-analysis-heading">
        <div>
          <p className="eyebrow">SCENE-BASED SPECTRAL ANALYSIS</p>
          <h3>Analyze this scene</h3>
        </div>
        {analysis?.status && (
          <span className={`scene-analysis-status status-${analysis.status}`} role="status">
            {analysis.status === 'processing' ? 'Processing' : analysis.status}
          </span>
        )}
      </div>
      {loadingTypes ? (
        <p className="processing-note" role="status">Checking supported analyses and scene bands…</p>
      ) : !analysisTypes.length ? (
        <p className="processing-note">No compatible analysis types were reported for this scene.</p>
      ) : (
        <form className="scene-analysis-form" onSubmit={submitAnalysis}>
          <label>
            <span>Analysis</span>
            <select
              aria-label="Scene analysis type"
              value={analysisType}
              onChange={(event) => changeType(event.target.value)}
            >
              {analysisTypes.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          {selectedType && <p className="scene-analysis-note">{selectedType.interpretation_note}</p>}
          {selectedType?.availability_note && (
            <p className="scene-analysis-note">{selectedType.availability_note}</p>
          )}
          {isIndex && selectedType && Object.entries(selectedType.roles).map(([role, label]) => (
            <label key={role}>
              <span>{label} band</span>
              <select
                aria-label={`${label} band`}
                value={mapping[role] ?? ''}
                onChange={(event) => setMapping((current) => ({ ...current, [role]: event.target.value }))}
              >
                <option value="">Select a source band</option>
                {bands.map((band) => (
                  <option key={band.index} value={band.index}>
                    {band.index} · {band.code ?? band.name ?? `Band ${band.index}`}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {analysisType === 'kmeans' && (
            <>
              <fieldset className="scene-feature-bands">
                <legend>Feature bands (select 2–16)</legend>
                {bands.map((band) => (
                  <label key={band.index}>
                    <input
                      type="checkbox"
                      checked={featureBands.includes(band.index)}
                      onChange={() => toggleFeatureBand(band.index)}
                    />
                    <span>{band.index} · {band.code ?? band.name ?? `Band ${band.index}`}</span>
                  </label>
                ))}
              </fieldset>
              <label>
                <span>Number of spectral clusters</span>
                <select
                  aria-label="Number of spectral clusters"
                  value={clusterCount}
                  onChange={(event) => setClusterCount(Number(event.target.value))}
                >
                  {[2, 3, 4, 5, 6, 7, 8, 9, 10].map((count) => (
                    <option key={count} value={count}>{count}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          <button className="secondary-button" type="submit" disabled={!canSubmit}>
            {submitting ? 'Analyzing scene…' : 'Run scene analysis'}
          </button>
          {submitting && <p className="processing-note" role="status">Processing raster windows and saving private outputs…</p>}
        </form>
      )}
      {error && <p className="imagery-error" role="alert">{error}</p>}
      {analysis?.status === 'failed' && analysis.error && (
        <p className="imagery-error" role="alert">{analysis.error}</p>
      )}
      {result && analysis.status === 'completed' && (
        <div className="scene-analysis-result" aria-live="polite">
          <h4>{selectedType?.name ?? analysis.analysis_type}</h4>
          <p>{result.formula ?? result.method}</p>
          {result.bounds && (
            <Suspense fallback={<p className="processing-note" role="status">Loading geospatial preview…</p>}>
              <MapView
                result={result}
                resultId={analysis.id}
                artifactName={result.preview?.artifact_name}
                layerName={selectedType?.name ?? analysis.analysis_type}
              />
            </Suspense>
          )}
          {statistics && (
            <>
              <dl className="scene-analysis-statistics">
                <div><dt>Valid pixels</dt><dd>{statistics.valid_pixels.toLocaleString()}</dd></div>
                <div><dt>Minimum</dt><dd>{statistics.min.toFixed(4)}</dd></div>
                <div><dt>Maximum</dt><dd>{statistics.max.toFixed(4)}</dd></div>
                <div><dt>Mean</dt><dd>{statistics.mean.toFixed(4)}</dd></div>
                <div><dt>Median</dt><dd>{statistics.median.toFixed(4)}</dd></div>
              </dl>
              {statistics.median_method && (
                <p className="scene-analysis-note">Median method: {statistics.median_method}</p>
              )}
            </>
          )}
          {result.classes && (
            <dl className="scene-analysis-statistics">
              {result.classes.map((item) => (
                <div key={item.value}>
                  <dt>{item.label}</dt>
                  <dd>{item.pixel_count.toLocaleString()} pixels · {(item.proportion * 100).toFixed(1)}%</dd>
                </div>
              ))}
            </dl>
          )}
          {result.legend && (
            <ul className="scene-analysis-legend" aria-label="Result legend">
              {result.legend.map((item) => (
                <li key={item.range ?? item.value}>
                  <span style={{ backgroundColor: item.color }} />
                  <b>{item.label}</b>
                  {item.range && <small>{item.range}</small>}
                </li>
              ))}
            </ul>
          )}
          <p className="scene-analysis-note">{result.interpretation_note}</p>
          <button
            className="imagery-download"
            type="button"
            disabled={downloading}
            onClick={downloadRaster}
          >
            {downloading ? 'Preparing raster…' : `Download ${result.raster?.artifact_name ?? 'analysis raster'}`}
          </button>
        </div>
      )}
    </section>
  );
}
