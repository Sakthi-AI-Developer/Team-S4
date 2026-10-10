import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import {
  compareImageryScenes,
  downloadArtifact,
  getImageryAnalysis,
} from '../services/api';

const MapView = lazy(() => import('./MapView'));

function errorMessage(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
  if (error.code === 'ECONNABORTED') {
    return 'Scene comparison timed out. Check the saved analysis status before retrying.';
  }
  return 'The two scenes could not be compared. The backend did not confirm a saved result.';
}

function suggestedMapping(scene) {
  const bands = scene?.metadata?.bands ?? [];
  const indexes = (code) => bands
    .filter((band) => band.code === code)
    .map((band) => band.index);
  const red = indexes('B04');
  const nir = indexes('B08');
  return {
    red: red.length === 1 ? String(red[0]) : '',
    nir: nir.length === 1 ? String(nir[0]) : '',
  };
}

function sceneOption(scene) {
  const metadata = scene.metadata ?? {};
  const filename = metadata.original_filename ?? scene.id;
  return `${metadata.acquisition_date ?? 'Date unavailable'} · ${filename}`;
}

function acquisitionTimestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return Number.NaN;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
    ? timestamp
    : Number.NaN;
}

function sensorFamily(scene) {
  const metadata = scene?.metadata ?? {};
  const value = `${metadata.sensor ?? ''} ${metadata.platform ?? ''}`.toLowerCase();
  if (value.includes('sentinel') || /\bs2[ab]\b/.test(value)) return 'sentinel-2';
  if (value.includes('landsat') || /\blc0[89]\b/.test(value)) return 'landsat';
  return '';
}

function formatArea(value) {
  return value == null ? 'Not available for this grid' : `${(value / 1_000_000).toFixed(4)} km²`;
}

export default function ChangeDetectionPanel({ scenes, loading = false, onAnalysisCompleted }) {
  const [baselineId, setBaselineId] = useState('');
  const [comparisonId, setComparisonId] = useState('');
  const [baselineBands, setBaselineBands] = useState({ red: '', nir: '' });
  const [comparisonBands, setComparisonBands] = useState({ red: '', nir: '' });
  const [threshold, setThreshold] = useState('0.1');
  const [analysis, setAnalysis] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [error, setError] = useState('');

  const baseline = useMemo(
    () => scenes.find((scene) => scene.id === baselineId),
    [scenes, baselineId],
  );
  const comparison = useMemo(
    () => scenes.find((scene) => scene.id === comparisonId),
    [scenes, comparisonId],
  );
  const baselineMetadata = baseline?.metadata ?? {};
  const comparisonMetadata = comparison?.metadata ?? {};
  const baselineDate = baselineMetadata.acquisition_date ?? '';
  const comparisonDate = comparisonMetadata.acquisition_date ?? '';
  const baselineTimestamp = acquisitionTimestamp(baselineDate);
  const comparisonTimestamp = acquisitionTimestamp(comparisonDate);
  const dateOrderValid = Boolean(
    baselineDate
    && comparisonDate
    && Number.isFinite(baselineTimestamp)
    && Number.isFinite(comparisonTimestamp)
    && comparisonTimestamp > baselineTimestamp,
  );
  const sensorMismatch = Boolean(
    baseline
    && comparison
    && sensorFamily(baseline)
    && sensorFamily(comparison)
    && sensorFamily(baseline) !== sensorFamily(comparison),
  );
  const thresholdValue = Number(threshold);
  const mappingsComplete = [baselineBands, comparisonBands].every(
    (mapping) => mapping.red !== ''
      && mapping.nir !== ''
      && Number.isInteger(Number(mapping.red))
      && Number.isInteger(Number(mapping.nir))
      && mapping.red !== mapping.nir,
  );
  const canSubmit = Boolean(
    baseline
    && comparison
    && baselineId !== comparisonId
    && dateOrderValid
    && !sensorMismatch
    && mappingsComplete
    && Number.isFinite(thresholdValue)
    && thresholdValue > 0
    && thresholdValue <= 2
    && !submitting,
  );

  useEffect(() => {
    setBaselineBands(suggestedMapping(baseline));
    setAnalysis(null);
    setError('');
  }, [baseline]);

  useEffect(() => {
    setComparisonBands(suggestedMapping(comparison));
    setAnalysis(null);
    setError('');
  }, [comparison]);

  useEffect(() => {
    if (!analysis?.id || analysis.status !== 'processing') return undefined;
    let active = true;
    let timer;
    const poll = async () => {
      try {
        const response = await getImageryAnalysis(analysis.id);
        if (!active) return;
        setAnalysis(response);
        if (response.status === 'processing') timer = setTimeout(poll, 2000);
      } catch (requestError) {
        if (active) setError(errorMessage(requestError));
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

  function updateBandSelection(setMapping, mapping, role, value) {
    setMapping({ ...mapping, [role]: value });
    setAnalysis(null);
  }

  async function submit(event) {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError('');
    setAnalysis(null);
    try {
      setAnalysis(await compareImageryScenes({
        baseline_scene_id: baselineId,
        comparison_scene_id: comparisonId,
        baseline_band_mapping: {
          red: Number(baselineBands.red),
          nir: Number(baselineBands.nir),
        },
        comparison_band_mapping: {
          red: Number(comparisonBands.red),
          nir: Number(comparisonBands.nir),
        },
        threshold: thresholdValue,
      }));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  async function download(name) {
    if (!analysis?.id || !name || downloading) return;
    setDownloading(name);
    setError('');
    try {
      const response = await downloadArtifact(analysis.id, name);
      const objectUrl = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = name;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setDownloading('');
    }
  }

  const result = analysis?.result;
  const statistics = result?.statistics;

  return (
    <section className="scene-change" aria-label="Two-scene change detection">
      <div className="scene-analysis-heading">
        <div>
          <p className="eyebrow">TEMPORAL COMPARISON</p>
          <h3>Detect candidate NDVI change</h3>
        </div>
        {analysis?.status && (
          <span className={`scene-analysis-status status-${analysis.status}`} role="status">
            {analysis.status}
          </span>
        )}
      </div>
      <p className="scene-analysis-note">
        Compare two dated GeoTIFF scenes on the baseline grid. Candidate changes are
        thresholded index differences, not confirmed environmental events.
      </p>
      {loading ? (
        <p className="processing-note" role="status">Loading saved scenes…</p>
      ) : !scenes.length ? (
        <p className="processing-note">Upload or save two dated imagery scenes to begin.</p>
      ) : (
        <>
        {scenes.length === 1 && (
          <p className="processing-note" role="status">
            Save a second scene with a valid later acquisition date to run a comparison.
          </p>
        )}
        <form className="scene-change-form" onSubmit={submit}>
          <label>
            <span>Baseline scene (earlier date)</span>
            <select
              aria-label="Baseline scene"
              value={baselineId}
              onChange={(event) => setBaselineId(event.target.value)}
            >
              <option value="">Select baseline scene</option>
              {scenes.map((scene) => (
                <option key={scene.id} value={scene.id}>{sceneOption(scene)}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Comparison scene (later date)</span>
            <select
              aria-label="Comparison scene"
              value={comparisonId}
              onChange={(event) => setComparisonId(event.target.value)}
            >
              <option value="">Select comparison scene</option>
              {scenes.map((scene) => (
                <option key={scene.id} value={scene.id}>{sceneOption(scene)}</option>
              ))}
            </select>
          </label>
          {baseline && (
            <p className="scene-analysis-note">
              Baseline date: {baselineDate || 'missing from source metadata'}
              {baselineMetadata.sensor ? ` · ${baselineMetadata.sensor}` : ''}
              {baselineMetadata.platform ? ` · ${baselineMetadata.platform}` : ''}
            </p>
          )}
          {comparison && (
            <p className="scene-analysis-note">
              Comparison date: {comparisonDate || 'missing from source metadata'}
              {comparisonMetadata.sensor ? ` · ${comparisonMetadata.sensor}` : ''}
              {comparisonMetadata.platform ? ` · ${comparisonMetadata.platform}` : ''}
            </p>
          )}
          {baseline && comparison && !dateOrderValid && (
            <p className="imagery-error" role="alert">
              Select two distinct scenes with valid dates, and set the comparison date later than the baseline date.
            </p>
          )}
          {sensorMismatch && (
            <p className="imagery-error" role="alert">
              The scenes report different sensor families. Select scenes from compatible sensors.
            </p>
          )}
          {baseline && comparison
            && (!baselineMetadata.sensor
              || !comparisonMetadata.sensor
              || !baselineMetadata.platform
              || !comparisonMetadata.platform) && (
            <p className="scene-analysis-note">
              Sensor or platform identity is missing or unverified for one or both source files; comparability cannot be confirmed automatically.
            </p>
          )}
          {baseline && (
            <fieldset className="scene-change-bands">
              <legend>Baseline NDVI band mapping</legend>
              {['red', 'nir'].map((role) => (
                <label key={`baseline-${role}`}>
                  <span>{role === 'red' ? 'Red' : 'Near infrared'}</span>
                  <select
                    aria-label={`Baseline ${role} band`}
                    value={baselineBands[role]}
                    onChange={(event) => updateBandSelection(
                      setBaselineBands,
                      baselineBands,
                      role,
                      event.target.value,
                    )}
                  >
                    <option value="">Choose source band</option>
                    {(baselineMetadata.bands ?? []).map((band) => (
                      <option key={band.index} value={band.index}>
                        {band.index} · {band.code ?? band.name ?? `Band ${band.index}`}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </fieldset>
          )}
          {comparison && (
            <fieldset className="scene-change-bands">
              <legend>Comparison NDVI band mapping</legend>
              {['red', 'nir'].map((role) => (
                <label key={`comparison-${role}`}>
                  <span>{role === 'red' ? 'Red' : 'Near infrared'}</span>
                  <select
                    aria-label={`Comparison ${role} band`}
                    value={comparisonBands[role]}
                    onChange={(event) => updateBandSelection(
                      setComparisonBands,
                      comparisonBands,
                      role,
                      event.target.value,
                    )}
                  >
                    <option value="">Choose source band</option>
                    {(comparisonMetadata.bands ?? []).map((band) => (
                      <option key={band.index} value={band.index}>
                        {band.index} · {band.code ?? band.name ?? `Band ${band.index}`}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </fieldset>
          )}
          <label>
            <span>Absolute NDVI-difference threshold (index units)</span>
            <input
              aria-label="NDVI difference threshold"
              type="number"
              min="0.001"
              max="2"
              step="any"
              value={threshold}
              onChange={(event) => {
                setThreshold(event.target.value);
                setAnalysis(null);
              }}
            />
          </label>
          <p className="scene-analysis-note">
            Pixels with |comparison NDVI − baseline NDVI| ≥ threshold are marked as candidate changes.
            Threshold suitability varies by sensor, season, biome, calibration, and registration.
          </p>
          <button className="secondary-button" type="submit" disabled={!canSubmit}>
            {submitting ? 'Comparing scenes…' : 'Run change detection'}
          </button>
          {submitting && (
            <p className="processing-note" role="status">
              Aligning to the baseline grid, comparing valid pixels, and saving private outputs…
            </p>
          )}
        </form>
        </>
      )}
      {error && <p className="imagery-error" role="alert">{error}</p>}
      {analysis?.status === 'failed' && analysis.error && (
        <p className="imagery-error" role="alert">{analysis.error}</p>
      )}
      {result && analysis.status === 'completed' && (
        <div className="scene-change-result" aria-live="polite">
          <h4>
            {analysis.baseline?.acquisition_date} → {analysis.comparison?.acquisition_date}
          </h4>
          <p>{result.method} · threshold {result.threshold} {result.threshold_units}</p>
          {result.bounds && (
            <Suspense fallback={<p className="processing-note" role="status">Loading comparison map…</p>}>
              <MapView
                result={result}
                resultId={analysis.id}
                artifactName={result.preview?.artifact_name}
                layerName="Candidate NDVI change"
              />
            </Suspense>
          )}
          {statistics && (
            <dl className="scene-analysis-statistics">
              <div><dt>Valid pixels</dt><dd>{result.valid_comparison_pixels.toLocaleString()} / {result.total_target_pixels.toLocaleString()} ({result.valid_pixel_percentage.toFixed(1)}%)</dd></div>
              <div><dt>Candidate changed</dt><dd>{result.changed_pixels.toLocaleString()} ({result.change_percentage_of_valid_pixels.toFixed(1)}%)</dd></div>
              <div><dt>Positive / negative</dt><dd>{result.positive_change_pixels.toLocaleString()} / {result.negative_change_pixels.toLocaleString()}</dd></div>
              <div><dt>Below threshold</dt><dd>{result.unchanged_pixels.toLocaleString()}</dd></div>
              <div><dt>Difference min / max</dt><dd>{statistics.min.toFixed(4)} / {statistics.max.toFixed(4)}</dd></div>
              <div><dt>Difference mean / median</dt><dd>{statistics.mean.toFixed(4)} / {statistics.median.toFixed(4)}</dd></div>
              <div><dt>Valid comparison area</dt><dd>{formatArea(result.valid_comparison_area_square_metres)}</dd></div>
              <div><dt>Changed area</dt><dd>{formatArea(result.changed_area_square_metres)}</dd></div>
            </dl>
          )}
          {result.legend && (
            <ul className="scene-analysis-legend" aria-label="Change detection legend">
              {result.legend.map((item) => (
                <li key={item.value}>
                  <span style={{ backgroundColor: item.color }} />
                  <b>{item.label}</b>
                </li>
              ))}
            </ul>
          )}
          <p className="scene-analysis-note">{result.nodata_policy}</p>
          {result.alignment && (
            <p className="scene-analysis-note">
              Grid: {result.alignment.target}; resampling: {result.alignment.resampling}.
              {result.alignment.comparison_was_reprojected_or_resampled
                ? ' Comparison scene was reprojected or resampled.'
                : ' Input grids already matched.'}
            </p>
          )}
          {[...(result.compatibility_warnings ?? []), ...(result.area_warnings ?? []), ...(result.warnings ?? [])]
            .map((warning) => <p className="scene-analysis-note" key={warning}>{warning}</p>)}
          <p className="scene-analysis-note">{result.limitations}</p>
          <div className="scene-change-downloads">
            <button
              className="imagery-download"
              type="button"
              disabled={Boolean(downloading)}
              onClick={() => download(result.raster?.artifact_name)}
            >
              {downloading === result.raster?.artifact_name ? 'Preparing download…' : 'Download NDVI difference GeoTIFF'}
            </button>
            <button
              className="imagery-download"
              type="button"
              disabled={Boolean(downloading)}
              onClick={() => download(result.change_mask?.artifact_name)}
            >
              {downloading === result.change_mask?.artifact_name ? 'Preparing download…' : 'Download candidate-change mask'}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
