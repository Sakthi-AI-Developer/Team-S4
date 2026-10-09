import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiRootUrl, downloadSatellite, getDataset, getGeoAIHistory, getGeoAIModelEvaluation, getGeoAIRiskIndicators, getGeoAIStatus, getHealth, getResults, getResult, getSatelliteStatus, runAllAnalysis, runChangeDetection, runGeoAILandCoverTransitions, runGeoAISpatialAnalysis, runGeoAIVegetationForecast, runLandcover, runNDBI, runNDVI, runNDWI, searchSatellite, uploadBand } from './services/api';
import AnalysisPanel from './components/AnalysisPanel';
import ChangeChart from './components/ChangeChart';
import DatasetSelector from './components/DatasetSelector';
import DownloadPanel from './components/DownloadPanel';
import ErrorMessage from './components/ErrorMessage';
import Header from './components/Header';
import InsightPanel from './components/InsightPanel';
import LandCoverChart from './components/LandCoverChart';
import Legend from './components/Legend';
import LoadingIndicator from './components/LoadingIndicator';
import MapView from './components/MapView';
import MetricCard from './components/MetricCard';

const layerConfig = [
  { key: 'ndvi', label: 'NDVI', color: '#83bf79' },
  { key: 'ndwi', label: 'NDWI', color: '#5da9e9' },
  { key: 'ndbi', label: 'NDBI', color: '#dc846e' },
  { key: 'landcover', label: 'Land Use', color: '#d0ad67' },
  { key: 'change_detection', label: 'Change Detection', color: '#ae7fd0' },
];
const analysisActions = {
  ndvi: runNDVI,
  ndwi: runNDWI,
  ndbi: runNDBI,
  landcover: runLandcover,
  change_detection: runChangeDetection,
};
const number = (value, digits = 3) => Number.isFinite(value) ? value.toFixed(digits) : '--';
const percent = (value) => Number.isFinite(value) ? `${value.toFixed(1)}%` : '--';

function analysisMapFromResponse(response) {
  if (response?.result) return {};
  return Object.fromEntries(
    layerConfig
      .filter(({ key }) => response?.[key]?.success)
      .map(({ key }) => [key, response[key]]),
  );
}

function friendlyError(error) {
  if (!error.response) return 'Backend is unavailable. Start the FastAPI server to enable satellite analysis.';
  const detail = error.response.data?.detail;
  return typeof detail === 'string' ? detail : 'Unable to process the selected dataset.';
}

function healthErrorMessage(error) {
  if (error.response) return `Backend health check failed with HTTP ${error.response.status}.`;
  if (error.code === 'ECONNABORTED') return 'Backend health check timed out. Check the API URL and try again.';
  if (!apiRootUrl) return 'Production API URL is not configured. Set VITE_API_URL and rebuild the frontend.';
  if (error.message?.includes('unexpected response')) return 'Backend health check returned an invalid response.';
  return 'Backend is unavailable. Check the configured API URL and network connection.';
}

function App() {
  const [backendStatus, setBackendStatus] = useState('checking');
  const [backendError, setBackendError] = useState('');
  const [dataset, setDataset] = useState(null);
  const [datasetLoading, setDatasetLoading] = useState(true);
  const [uploadingPeriod, setUploadingPeriod] = useState('');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [analyses, setAnalyses] = useState({});
  const [resultId, setResultId] = useState(null);
  const [recentResults, setRecentResults] = useState([]);
  const [resultLoading, setResultLoading] = useState(false);
  const [activeLayer, setActiveLayer] = useState('ndvi');
  const [dataSource, setDataSource] = useState('local');
  const [satelliteStatus, setSatelliteStatus] = useState({ configured: false, provider: 'local', message: 'Stage-A local dataset mode is active.' });
  const [geoAIStatus, setGeoAIStatus] = useState({ status: 'checking', available: false, components: [] });
  const [geoAIInsights, setGeoAIInsights] = useState({
    history: null,
    forecast: null,
    transitions: null,
    spatial: null,
    risk: null,
    evaluation: null,
  });
  const [aoi, setAoi] = useState({
    type: 'Polygon',
    coordinates: [[[-1, 50], [-1, 51], [0, 51], [0, 50], [-1, 50]]],
  });
  const [searchWindow, setSearchWindow] = useState({ startDate: '2024-01-01', endDate: '2024-01-15', maxCloudCover: 20 });
  const [satelliteResults, setSatelliteResults] = useState([]);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [liveLoading, setLiveLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [isDemoMode, setIsDemoMode] = useState(false);

  const refreshDataset = useCallback(async () => {
    setDatasetLoading(true);
    try {
      setDataset(await getDataset());
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setDatasetLoading(false);
    }
  }, []);

  const refreshResults = useCallback(async () => {
    try {
      const response = await getResults();
      setRecentResults(response.results ?? []);
    } catch {
      setRecentResults([]);
    }
  }, []);

  const loadGeoAIInsights = useCallback(async () => {
    try {
      const status = await getGeoAIStatus();
      setGeoAIStatus(status);
    } catch (error) {
      console.error('Failed to fetch GeoAI status', error);
      setGeoAIStatus({ status: 'unavailable', available: false, components: [] });
    }

    try {
      const [history, forecast, transitions, spatial, risk, evaluation] = await Promise.all([
        getGeoAIHistory().catch(() => ({ success: false, status: 'insufficient-data', message: 'Historic observations are unavailable.' })),
        runGeoAIVegetationForecast({ horizon_days: 90 }).catch(() => ({ success: false, status: 'insufficient-data', message: 'Vegetation forecast is unavailable.' })),
        runGeoAILandCoverTransitions({ current_dataset_id: 'current', historical_dataset_id: 'historical', include_area: true }).catch(() => ({ success: false, status: 'insufficient-data', message: 'Land-cover transitions are unavailable.' })),
        runGeoAISpatialAnalysis({ dataset_id: 'current', include_history: true, block_size: 10 }).catch(() => ({ success: false, status: 'insufficient-data', message: 'Spatial analysis is unavailable.' })),
        getGeoAIRiskIndicators().catch(() => ({ success: false, status: 'insufficient-data', message: 'Risk indicators are unavailable.' })),
        getGeoAIModelEvaluation().catch(() => ({ success: false, status: 'insufficient-data', message: 'Model evaluation is unavailable.' })),
      ]);
      setGeoAIInsights({ history, forecast, transitions, spatial, risk, evaluation });
    } catch (error) {
      console.error('Failed to refresh GeoAI insights', error);
      setGeoAIInsights({ history: null, forecast: null, transitions: null, spatial: null, risk: null, evaluation: null });
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    async function initialize() {
      const healthCheck = getHealth()
        .then(() => { if (mounted) setBackendStatus('online'); })
        .catch((error) => {
          if (mounted) {
            setBackendStatus('offline');
            setBackendError(healthErrorMessage(error));
          }
        });
      const datasetCheck = refreshDataset();
      const resultsCheck = refreshResults();
      const satelliteCheck = getSatelliteStatus()
        .then((response) => { if (mounted) setSatelliteStatus(response); })
        .catch(() => { if (mounted) setSatelliteStatus({ configured: false, provider: 'local', message: 'Live satellite provider is not configured.' }); });
      const geoAIStatusCheck = loadGeoAIInsights();
      await Promise.all([healthCheck, datasetCheck, resultsCheck, satelliteCheck, geoAIStatusCheck]);
    }
    initialize();
    return () => { mounted = false; };
  }, [loadGeoAIInsights, refreshDataset, refreshResults]);

  const current = analyses.ndvi;
  const ndwi = analyses.ndwi;
  const ndbi = analyses.ndbi;
  const landcover = analyses.landcover;
  const change = analyses.change_detection;
  const selected = analyses[activeLayer];
  const mapLayer = layerConfig.find((layer) => layer.key === activeLayer);

  const insights = useMemo(() => {
    const messages = [];
    if (change?.mean_change < 0) messages.push('Satellite-derived vegetation index is lower than the historical reference period.');
    if (change?.mean_change > 0) messages.push('Satellite-derived vegetation index is higher than the historical reference period.');
    if (current?.vegetation_percentage >= 50) messages.push('Based on available imagery, a relatively high share of valid pixels is above the configured vegetation-index threshold.');
    if (ndbi?.built_up_indicator_percentage > 50) messages.push('The satellite-derived built-up indicator is positive for a relatively large share of valid pixels; this is not definitive building detection.');
    if (!messages.length && !current) messages.push('Run an analysis to generate satellite-derived indicators and summaries.');
    if (!messages.length && current) messages.push('Indicators are calculated from available imagery; the results require ground validation where applicable.');
    return messages;
  }, [change, current, ndbi]);

  const acceptAllResponse = (response) => {
    const successful = Object.fromEntries(
      Object.entries(analysisMapFromResponse(response))
        .map(([key, value]) => [key, { ...value, result_id: response.id }]),
    );
    setAnalyses((previous) => ({ ...previous, ...successful }));
    const unavailable = layerConfig
      .filter(({ key }) => response[key] && !response[key].success)
      .map(({ label, key }) => `${label}: ${response[key].message}`);
    setNotice(unavailable.length ? `Some analyses were unavailable. ${unavailable.join(' ')}` : 'Analysis complete. Results are calculated from the available local rasters.');
  };

  async function handleAnalysis(key) {
    setBusy(key);
    setError('');
    setNotice('');
    try {
      const response = await analysisActions[key]();
      setAnalyses((previous) => ({
        ...previous,
        [key]: { ...response.result, result_id: response.id },
      }));
      setResultId(response.id);
      setNotice(`${response.analysis} completed using local satellite imagery.`);
      await refreshResults();
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy('');
    }
  }

  async function handleAllAnalysis() {
    setBusy('all');
    setError('');
    setNotice('');
    try {
      const response = await runAllAnalysis();
      setResultId(response.id);
      acceptAllResponse(response);
      await refreshResults();
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy('');
    }
  }

  async function handleUpload(period, files) {
    if (uploadingPeriod) return;
    setUploadingPeriod(period);
    setUploadProgress(0);
    setError('');
    setNotice('');
    let uploaded = 0;
    const failures = [];
    try {
      for (const [index, file] of files.entries()) {
        try {
          await uploadBand(period, file, (event) => {
            if (event.total) {
              const fileProgress = Math.round((event.loaded / event.total) * 100);
              setUploadProgress(Math.round(((index + fileProgress / 100) / files.length) * 100));
            }
          });
          uploaded += 1;
        } catch (uploadError) {
          const detail = uploadError.response?.data?.detail;
          failures.push(`${file.name}: ${typeof detail === 'string' ? detail : 'Upload failed.'}`);
        }
      }
      await refreshDataset();
      if (uploaded) {
        setNotice(`${uploaded} GeoTIFF ${uploaded === 1 ? 'band was' : 'bands were'} added to the ${period} dataset.`);
      }
      if (failures.length) setError(failures.join(' '));
    } finally {
      setUploadingPeriod('');
      setUploadProgress(0);
    }
  }

  async function handleSelectResult(event) {
    const id = event.target.value;
    if (!id) return;
    setResultLoading(true);
    setError('');
    try {
      const response = await getResult(id);
      setResultId(id);
      if (response.result) {
        const analysisTitle = response.result.analysis?.toLowerCase() ?? '';
        const matching = layerConfig.find(({ key, label }) =>
          analysisTitle.includes(label.toLowerCase())
          || (key === 'landcover' && analysisTitle.includes('land-cover'))
          || (key === 'change_detection' && analysisTitle.includes('change')),
        );
        if (matching) {
          setAnalyses((previous) => ({
            ...previous,
            [matching.key]: { ...response.result, result_id: id },
          }));
        }
      } else {
        const loaded = Object.fromEntries(
          Object.entries(analysisMapFromResponse(response))
            .map(([key, value]) => [key, { ...value, result_id: id }]),
        );
        setAnalyses((previous) => ({ ...previous, ...loaded }));
      }
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setResultLoading(false);
    }
  }

  async function handleSatelliteSearch(event) {
    event.preventDefault();
    setLiveLoading(true);
    setError('');
    setNotice('');
    try {
      const response = await searchSatellite({
        aoi,
        start_date: searchWindow.startDate,
        end_date: searchWindow.endDate,
        max_cloud_cover: Number(searchWindow.maxCloudCover),
      });
      if (!response.results?.length) {
        setSatelliteResults([]);
        setNotice('No scenes were found for the selected AOI and date range.');
        return;
      }
      setSatelliteResults(response.results);
      setSelectedProductId(response.results[0].product_id);
      setNotice('Satellite scenes were found. Select a product to use it in the analysis pipeline.');
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'Live search is unavailable.');
    } finally {
      setLiveLoading(false);
    }
  }

  async function handleSatelliteDownload() {
    if (!selectedProductId) {
      setError('Select a satellite product before downloading.');
      return;
    }
    setLiveLoading(true);
    setError('');
    setNotice('');
    try {
      const response = await downloadSatellite({ product_id: selectedProductId, aoi });
      setNotice(`Scene ${selectedProductId} is ready for analysis.`);
      setDataSource('local');
      await refreshDataset();
      setSatelliteStatus((previous) => ({ ...previous, provider: 'live', configured: true, message: `Scene ${selectedProductId} downloaded and is ready for analysis.` }));
      if (response?.download?.success) {
        setNotice(`Scene ${selectedProductId} downloaded and cached successfully.`);
      }
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'Satellite product download failed.');
    } finally {
      setLiveLoading(false);
    }
  }

  const datasetAvailable = Boolean(dataset?.current?.available);

  const handleDemoMode = useCallback(async () => {
    setIsDemoMode(true);
    setNotice('Demo mode enabled. Using the local dataset workflow without external dependencies.');
    setDataSource('local');
    try {
      await refreshDataset();
    } catch {
      setError('Demo mode could not refresh the local dataset state.');
    }
  }, [refreshDataset]);

  const handleReset = useCallback(() => {
    setIsDemoMode(false);
    setError('');
    setNotice('Workspace reset. The app is ready to start a fresh local analysis.');
    setAnalyses({});
    setResultId(null);
    setSatelliteResults([]);
    setSelectedProductId('');
    setBusy('');
    setDataSource('local');
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top"><span className="brand-mark">S</span><span>ORBITAL<span className="brand-light"> / INTEL</span></span></a>
        <p className="nav-label">WORKSPACE</p>
        <a className="nav-item active" href="#overview"><span>◫</span> Dashboard</a>
        <a className="nav-item" href="#dataset"><span>▤</span> Dataset</a>
        <a className="nav-item" href="#analysis"><span>◎</span> Analysis</a>
        <a className="nav-item" href="#results"><span>↗</span> Results</a>
        <a className="nav-item" href="#about"><span>ⓘ</span> Methodology</a>
        <div className="sidebar-bottom">
          <span className={`status-dot ${backendStatus === 'online' ? 'online' : ''}`} />
          <div><b>{backendStatus === 'online' ? 'System Online' : backendStatus === 'checking' ? 'Checking backend' : 'Backend Offline'}</b><small>Stage A · Local imagery</small></div>
        </div>
      </aside>

      <main id="top" className="main-content">
        <Header backendStatus={backendStatus} />
        <section id="overview" className="page-heading">
          <div><p className="eyebrow">EARTH OBSERVATION / ANALYSIS</p><h1>Satellite Intelligence</h1><p>AI-assisted analysis of pre-downloaded satellite imagery.</p></div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button type="button" className="secondary-button" onClick={handleDemoMode}>Demo mode</button>
            <button type="button" className="secondary-button" onClick={handleReset}>Reset</button>
            {resultLoading && <LoadingIndicator label="Loading saved result" />}
          </div>
        </section>

        {backendStatus === 'offline' && <ErrorMessage message={backendError} />}
        {error && backendStatus !== 'offline' && <ErrorMessage message={error} onDismiss={() => setError('')} />}
        {notice && <div className="alert info" role="status"><span>i</span><p>{notice}</p><button onClick={() => setNotice('')} aria-label="Dismiss notice">×</button></div>}

        <section id="dataset">
          <div className="panel dataset-panel" aria-label="Data source selector">
            <div className="panel-header">
              <div><p className="eyebrow">DATA SOURCE</p><h2>Satellite data mode</h2></div>
            </div>
            <div className="analysis-actions" style={{ paddingTop: 12 }}>
              <button type="button" className={`secondary-button ${dataSource === 'local' ? 'selected' : ''}`} onClick={() => setDataSource('local')}>Local Dataset</button>
              <button type="button" className={`secondary-button ${dataSource === 'live' ? 'selected' : ''}`} onClick={() => setDataSource('live')}>Live Satellite</button>
            </div>
            {dataSource === 'local' ? (
              <DatasetSelector
                dataset={dataset}
                loading={datasetLoading}
                uploadingPeriod={uploadingPeriod ? `Uploading ${uploadProgress}%` : ''}
                onUpload={handleUpload}
                onRefresh={refreshDataset}
              />
            ) : (
              <div className="panel" style={{ margin: '0 15px 12px', padding: 14, border: '1px solid #2b3a3c', borderRadius: 8 }}>
                <div className="panel-header">
                  <div><p className="eyebrow">LIVE SATELLITE DATA</p><h2>Search for scenes</h2></div>
                </div>
                <p className="processing-note" style={{ margin: '0 0 10px' }}>{satelliteStatus.message}</p>
                <form onSubmit={handleSatelliteSearch} style={{ display: 'grid', gap: 10 }}>
                  <label style={{ display: 'grid', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#9aaea3' }}>AOI GeoJSON</span>
                    <textarea value={JSON.stringify(aoi, null, 2)} onChange={(event) => { try { setAoi(JSON.parse(event.target.value)); } catch { setAoi((previous) => previous); } }} rows={6} style={{ width: '100%', background: '#121d20', color: '#edf5f2', border: '1px solid #2b3a3c', borderRadius: 6, padding: 10 }} />
                  </label>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ fontSize: 11, color: '#9aaea3' }}>Start date</span>
                      <input type="date" value={searchWindow.startDate} onChange={(event) => setSearchWindow((previous) => ({ ...previous, startDate: event.target.value }))} style={{ background: '#121d20', color: '#edf5f2', border: '1px solid #2b3a3c', borderRadius: 6, padding: 8 }} />
                    </label>
                    <label style={{ display: 'grid', gap: 6 }}>
                      <span style={{ fontSize: 11, color: '#9aaea3' }}>End date</span>
                      <input type="date" value={searchWindow.endDate} onChange={(event) => setSearchWindow((previous) => ({ ...previous, endDate: event.target.value }))} style={{ background: '#121d20', color: '#edf5f2', border: '1px solid #2b3a3c', borderRadius: 6, padding: 8 }} />
                    </label>
                  </div>
                  <label style={{ display: 'grid', gap: 6 }}>
                    <span style={{ fontSize: 11, color: '#9aaea3' }}>Maximum cloud cover ({searchWindow.maxCloudCover}%)</span>
                    <input type="range" min="0" max="100" value={searchWindow.maxCloudCover} onChange={(event) => setSearchWindow((previous) => ({ ...previous, maxCloudCover: Number(event.target.value) }))} />
                  </label>
                  <button className="primary-button" type="submit" disabled={liveLoading || !satelliteStatus.configured}>
                    {liveLoading ? 'Searching…' : 'SEARCH SATELLITE DATA'}
                  </button>
                </form>
                {satelliteResults.length > 0 && (
                  <div style={{ display: 'grid', gap: 8, marginTop: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 12, letterSpacing: '.12em', color: '#819294' }}>AVAILABLE SCENES</h3>
                    {satelliteResults.map((product) => (
                      <div key={product.product_id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: 10, border: '1px solid #2b3a3c', borderRadius: 6, background: '#172124' }}>
                        <div>
                          <div style={{ fontWeight: 700 }}>{product.acquisition_date}</div>
                          <small style={{ color: '#8fa0a0' }}>{product.cloud_cover ?? 'N/A'}% cloud · {product.platform}</small>
                          <div style={{ color: '#b7c7c4', fontSize: 11 }}>{product.product_id}</div>
                        </div>
                        <button type="button" className="secondary-button" onClick={() => setSelectedProductId(product.product_id)}>{selectedProductId === product.product_id ? 'Selected' : 'Select'}</button>
                      </div>
                    ))}
                    {selectedProductId && (
                      <button type="button" className="primary-button" onClick={handleSatelliteDownload} disabled={liveLoading}>USE THIS SCENE</button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        <section id="analysis"><AnalysisPanel busy={busy || uploadingPeriod} onRun={handleAnalysis} onRunAll={handleAllAnalysis} datasetAvailable={datasetAvailable && backendStatus === 'online' && !uploadingPeriod} /></section>

        <section id="map" className="map-panel panel">
          <div className="panel-header">
            <div><p className="eyebrow">SPATIAL VIEW</p><h2>Analysis map</h2></div>
            <div className="layer-controls" aria-label="Map layers">
              {layerConfig.map((layer) => (
                <button key={layer.key} className={`layer-button ${activeLayer === layer.key ? 'selected' : ''}`} onClick={() => setActiveLayer(layer.key)} disabled={!analyses[layer.key]}>
                  <i style={{ backgroundColor: layer.color }} />{layer.label}
                </button>
              ))}
            </div>
          </div>
          {busy && <div className="map-loading"><LoadingIndicator label="Processing satellite raster…" /></div>}
          <MapView result={selected} layerName={mapLayer?.label} resultId={resultId} />
          <div className="map-footer"><Legend layer={activeLayer} /><span>{selected?.statistics?.valid_pixels?.toLocaleString() ?? '--'} valid pixels</span></div>
        </section>

        <section className="metric-grid" aria-label="Key metrics">
          <MetricCard title="NDVI mean" icon="⌁" value={number(current?.statistics?.mean)} subtitle={`Min ${number(current?.statistics?.min)} · Max ${number(current?.statistics?.max)}`} />
          <MetricCard title="NDWI mean" icon="≈" value={number(ndwi?.statistics?.mean)} subtitle={`Water-related indicator · ${percent(ndwi?.water_related_percentage)}`} />
          <MetricCard title="NDBI mean" icon="⌂" value={number(ndbi?.statistics?.mean)} subtitle={`Built-up indicator · ${percent(ndbi?.built_up_indicator_percentage)}`} />
          <MetricCard title="Vegetation share" icon="↗" value={percent(current?.vegetation_percentage)} subtitle={`Threshold ${number(current?.vegetation_threshold, 2)} · NDVI`} />
        </section>
        <section className="metric-grid secondary-metrics">
          <MetricCard title="Valid NDVI pixels" icon="▦" value={current?.statistics?.valid_pixels?.toLocaleString() ?? '--'} subtitle="Nodata excluded" />
          <MetricCard title="Water-related share" icon="≈" value={percent(ndwi?.water_related_percentage)} subtitle="NDWI threshold indicator" />
          <MetricCard title="Built-up indicator" icon="⌂" value={percent(ndbi?.built_up_indicator_percentage)} subtitle="Not definitive building detection" />
          <MetricCard title="Mean NDVI change" icon="Δ" value={number(change?.mean_change)} subtitle={change ? `Changed pixels ${percent(change.changed_pixel_percentage)}` : 'Historical data required'} />
        </section>

        <section className="panel" aria-label="Predictive GeoAI dashboard">
          <div className="panel-header">
            <div><p className="eyebrow">PREDICTIVE GEOAI</p><h2>Vegetation and risk outlook</h2></div>
            <div className="processing-note" style={{ fontSize: 11 }}>
              {geoAIStatus?.available ? `${geoAIStatus.status.toUpperCase()} · ${geoAIStatus.components?.length ?? 0} components active` : 'GeoAI unavailable'}
            </div>
          </div>
          <div className="content-grid" style={{ marginTop: 12 }}>
            <div className="panel chart-panel">
              <div className="panel-header"><div><p className="eyebrow">FORECAST</p><h2>NDVI outlook</h2></div></div>
              {geoAIInsights.forecast?.success ? (
                <div className="ndvi-stat-grid">
                  <div className="ndvi-stat"><small>Trend</small><b>{number(geoAIInsights.forecast?.historical_trend?.slope_per_day, 4)}</b></div>
                  <div className="ndvi-stat"><small>Latest</small><b>{number(geoAIInsights.forecast?.forecast_values?.at(-1), 3)}</b></div>
                  <div className="ndvi-stat"><small>Horizon</small><b>{geoAIInsights.forecast?.forecast_dates?.length ?? 0} days</b></div>
                  <div className="ndvi-stat"><small>Interval support</small><b>{geoAIInsights.forecast?.prediction_interval?.support ? 'Yes' : 'Limited'}</b></div>
                </div>
              ) : (
                <div className="chart-empty">Forecast requires at least two viable historical NDVI observations.</div>
              )}
              <p className="panel-note">{geoAIInsights.forecast?.limitations ?? 'No forecast generated yet.'}</p>
            </div>

            <div className="panel chart-panel">
              <div className="panel-header"><div><p className="eyebrow">HISTORY</p><h2>Observation summary</h2></div></div>
              {geoAIInsights.history?.success ? (
                <div className="ndvi-stat-grid">
                  <div className="ndvi-stat"><small>Mean NDVI</small><b>{number(geoAIInsights.history?.mean_ndvi, 3)}</b></div>
                  <div className="ndvi-stat"><small>Latest</small><b>{number(geoAIInsights.history?.latest_ndvi, 3)}</b></div>
                  <div className="ndvi-stat"><small>Samples</small><b>{geoAIInsights.history?.count ?? 0}</b></div>
                  <div className="ndvi-stat"><small>Period</small><b>{geoAIInsights.history?.start_date ?? '--'} → {geoAIInsights.history?.end_date ?? '--'}</b></div>
                </div>
              ) : (
                <div className="chart-empty">Historic observations are unavailable or insufficient for analysis.</div>
              )}
            </div>

            <div className="panel chart-panel">
              <div className="panel-header"><div><p className="eyebrow">TRANSITIONS</p><h2>Land-cover change</h2></div></div>
              {geoAIInsights.transitions?.success ? (
                <div className="ndvi-stat-grid">
                  <div className="ndvi-stat"><small>Valid pixels</small><b>{geoAIInsights.transitions?.valid_pixel_count?.toLocaleString() ?? '--'}</b></div>
                  <div className="ndvi-stat"><small>Transitions</small><b>{geoAIInsights.transitions?.transitions?.length ?? 0}</b></div>
                  <div className="ndvi-stat"><small>Largest shift</small><b>{geoAIInsights.transitions?.transitions?.[0]?.pixel_count ?? 0}</b></div>
                  <div className="ndvi-stat"><small>Source</small><b>{geoAIInsights.transitions?.transitions?.[0]?.source_label ?? 'n/a'}</b></div>
                </div>
              ) : (
                <div className="chart-empty">Land-cover transitions are unavailable without aligned historical and current rasters.</div>
              )}
            </div>

            <div className="panel chart-panel">
              <div className="panel-header"><div><p className="eyebrow">RISK</p><h2>Indicator summary</h2></div></div>
              {geoAIInsights.risk?.success ? (
                <div className="ndvi-stat-grid">
                  <div className="ndvi-stat"><small>Indicators</small><b>{geoAIInsights.risk?.count ?? 0}</b></div>
                  <div className="ndvi-stat"><small>Highest severity</small><b>{geoAIInsights.risk?.indicators?.[0]?.severity ?? 'n/a'}</b></div>
                  <div className="ndvi-stat"><small>Focus</small><b>{geoAIInsights.risk?.indicators?.[0]?.indicator_name ?? 'n/a'}</b></div>
                  <div className="ndvi-stat"><small>Confidence</small><b>{geoAIInsights.risk?.indicators?.[0]?.data_quality?.status ?? 'n/a'}</b></div>
                </div>
              ) : (
                <div className="chart-empty">No actionable risk indicators are available yet.</div>
              )}
            </div>
          </div>
        </section>

        <section className="content-grid">
          <div className="panel chart-panel ndvi-panel">
            <div className="panel-header"><div><p className="eyebrow">VEGETATION INDEX</p><h2>NDVI statistics</h2></div></div>
            {current ? <div className="ndvi-stat-grid">
              {[
                ['Mean', current.statistics?.mean],
                ['Minimum', current.statistics?.min],
                ['Maximum', current.statistics?.max],
                ['Median', current.statistics?.median],
                ['Valid pixels', current.statistics?.valid_pixels],
                ['Vegetation share', current.vegetation_percentage],
              ].map(([label, value]) => <div className="ndvi-stat" key={label}><small>{label}</small><b>{label === 'Valid pixels' ? (value?.toLocaleString() ?? '--') : label === 'Vegetation share' ? percent(value) : number(value)}</b></div>)}
              <div className="ndvi-legend"><span>Very low</span><i /><span>Low</span><i /><span>Moderate</span><i /><span>High</span><i /><span>Very high</span></div>
            </div> : <div className="chart-empty">NDVI statistics will appear after calculating B04 and B08.</div>}
            <p className="panel-note">Index ranges are contextual indicators, not universal crop-health categories.</p>
          </div>
          <LandCoverChart result={landcover} />
          <ChangeChart result={change} currentResult={current} />
          <InsightPanel messages={insights} />
        </section>

        <section id="results" className="bottom-grid">
          <section className="panel downloads-panel">
            <div className="panel-header">
              <div><p className="eyebrow">SAVED OUTPUTS</p><h2>Recent results</h2></div>
              <select className="result-select" aria-label="Load saved result" onChange={handleSelectResult} value="">
                <option value="">Select a saved result</option>
                {recentResults.map((item) => <option value={item.id} key={item.id}>{item.analysis} · {new Date(item.created_at).toLocaleString()}</option>)}
              </select>
            </div>
            {resultId && <p className="panel-note">Current result ID: <code>{resultId}</code></p>}
            <DownloadPanel resultId={resultId} analyses={analyses} onError={setError} />
          </section>
          <article className="panel about-panel" id="about">
            <div className="panel-header"><div><p className="eyebrow">METHOD</p><h2>How to interpret results</h2></div></div>
            <p>Local GeoTIFF bands are validated and masked for nodata before spectral indices are calculated. A transparent index-threshold baseline supports land-cover categories; aligned historical rasters enable NDVI comparison.</p>
            <div className="formula-list">
              <span><b>NDVI</b> (B08 − B04) / (B08 + B04)</span>
              <span><b>NDWI</b> McFeeters: (B03 − B08) / (B03 + B08)</span>
              <span><b>NDBI</b> (B11 − B08) / (B11 + B08)</span>
            </div>
            <p className="panel-note">Satellite-derived indicators are not ground truth. Validate with field observations where needed. Stage B/live imagery is not enabled.</p>
          </article>
        </section>

        <footer className="footer"><span>ORBITAL / INTEL</span><span>Scientific indicators · Stage A offline processing</span><span>Backend: {backendStatus === 'online' ? 'Connected' : backendStatus === 'checking' ? 'Checking' : 'Offline'}</span></footer>
      </main>
    </div>
  );
}

export default App;
