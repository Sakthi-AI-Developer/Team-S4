import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ImageOverlay,
  MapContainer,
  Pane,
  ScaleControl,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import {
  downloadArtifact,
  downloadResult,
  getImageryScene,
  getResult,
  getResultArtifacts,
  getResultImage,
} from '../services/api';

const LEGACY_IMAGE_KEYS = new Set(['ndvi', 'ndwi', 'ndbi', 'landcover', 'change_detection']);

function validBounds(bounds) {
  return Array.isArray(bounds)
    && bounds.length === 4
    && bounds.every((value) => Number.isFinite(Number(value)))
    && Number(bounds[0]) >= -180
    && Number(bounds[2]) <= 180
    && Number(bounds[1]) >= -90
    && Number(bounds[3]) <= 90
    && Number(bounds[0]) < Number(bounds[2])
    && Number(bounds[1]) < Number(bounds[3]);
}

function leafletBounds(bounds) {
  return [[Number(bounds[1]), Number(bounds[0])], [Number(bounds[3]), Number(bounds[2])]];
}

function readableAnalysis(value) {
  const key = String(value ?? 'analysis_result').replace(/^scene_/, '').toLowerCase();
  const knownLabels = {
    change_detection: 'Candidate change detection',
    kmeans: 'K-means clusters',
    landcover: 'Land-cover baseline',
    ndvi: 'NDVI',
    ndwi: 'NDWI',
    ndbi: 'NDBI',
  };
  return knownLabels[key]
    ?? key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function resultLayer(summary, scenes) {
  const output = summary?.result && typeof summary.result === 'object'
    ? summary.result
    : summary;
  const bounds = output?.bounds ?? summary?.bounds;
  const previewArtifact = output?.preview?.artifact_name
    ?? summary?.preview?.artifact_name;
  const imageKey = summary?.visualization_url?.split('/').at(-1);
  if (!validBounds(bounds) || (!previewArtifact && !LEGACY_IMAGE_KEYS.has(imageKey))) {
    throw new Error('This result has no supported georeferenced preview to display.');
  }

  const analysis = summary.analysis ?? summary.analysis_type ?? 'Analysis result';
  const inputScenes = [
    summary.scene,
    summary.baseline,
    summary.comparison,
  ].filter((item) => typeof item?.id === 'string');
  const sceneMetadata = inputScenes.map((item) => ({
    ...item,
    savedScene: scenes.find((scene) => scene.id === item.id),
  }));
  const sceneNames = sceneMetadata.map((item) =>
    item.savedScene?.metadata?.original_filename
    ?? item.filename
    ?? item.id,
  );
  const dates = sceneMetadata.map((item) =>
    item.savedScene?.metadata?.acquisition_date
    ?? item.acquisition_date
    ?? 'date unknown',
  );
  const dateLabel = dates.length === 2 ? `${dates[0]} → ${dates[1]}` : dates[0];
  const rasterArtifact = output?.raster?.artifact_name
    ?? output?.change_mask?.artifact_name
    ?? null;
  return {
    id: `result:${summary.id}`,
    resultId: summary.id,
    name: `${readableAnalysis(analysis)} · ${dateLabel ?? 'date unavailable'}`,
    kind: 'Analysis result',
    analysis,
    sceneId: inputScenes.map((item) => item.id).join(', ') || null,
    sceneName: sceneNames.join(' → ') || null,
    acquisitionDate: dateLabel ?? null,
    bounds: bounds.map(Number),
    sourceBounds: validBounds(output?.raster?.bounds_wgs84)
      ? output.raster.bounds_wgs84.map(Number)
      : null,
    overlayCrs: 'EPSG:4326',
    sourceCrs: output?.raster?.crs ?? 'Not reported',
    dimensions: output?.raster?.width && output?.raster?.height
      ? `${output.raster.width} × ${output.raster.height}`
      : 'Not reported',
    resolution: output?.raster?.resolution ?? null,
    bands: output?.raster?.bands ?? null,
    previewArtifact,
    imageKey: previewArtifact ? null : imageKey,
    rasterArtifact,
    legacyDownloadKey: !rasterArtifact && LEGACY_IMAGE_KEYS.has(imageKey) ? imageKey : null,
    legend: output?.legend ?? output?.classes ?? null,
    interpretation: output?.interpretation_note ?? output?.limitations ?? '',
  };
}

function sourceLayer(scene) {
  const metadata = scene?.metadata ?? {};
  const bounds = metadata.preview?.bounds_wgs84;
  if (!validBounds(bounds) || metadata.preview?.crs !== 'EPSG:4326') {
    throw new Error(
      'This saved scene does not have a georeferenced map preview. Re-ingest it to create an aligned preview.',
    );
  }
  return {
    id: `scene:${scene.id}`,
    resultId: scene.id,
    name: `${metadata.original_filename ?? 'Saved scene'} · source preview`,
    kind: 'Source imagery preview',
    sceneId: scene.id,
    sceneName: metadata.original_filename ?? scene.id,
    acquisitionDate: metadata.acquisition_date ?? null,
    bounds: bounds.map(Number),
    sourceBounds: validBounds(metadata.extent_wgs84)
      ? metadata.extent_wgs84.map(Number)
      : null,
    overlayCrs: 'EPSG:4326',
    sourceCrs: metadata.crs ?? 'Not reported',
    dimensions: `${metadata.width} × ${metadata.height}`,
    resolution: metadata.resolution ?? null,
    bands: metadata.bands ?? null,
    previewArtifact: 'preview.png',
    imageKey: null,
    rasterArtifact: null,
    sourceDownload: 'source.tif',
    legend: null,
    interpretation: metadata.preview?.description ?? '',
    persistence: scene.persistence?.mode ?? 'unknown',
  };
}

function MapActions({ fitRequest, onLocation }) {
  const map = useMap();
  useEffect(() => {
    if (!fitRequest) return;
    if (fitRequest.mode === 'reset') {
      map.setView([0, 0], 2);
    } else if (fitRequest.bounds) {
      map.fitBounds(fitRequest.bounds, { padding: [24, 24], maxZoom: 14 });
    }
  }, [fitRequest, map]);
  useMapEvents({
    click(event) {
      onLocation([event.latlng.lng, event.latlng.lat]);
    },
  });
  return null;
}

function RasterLayer({ layer, paneName, cache, status, onStatus, retryVersion }) {
  const [imageUrl, setImageUrl] = useState('');
  const cacheKey = `${layer.resultId}:${layer.previewArtifact ?? layer.imageKey}`;

  useEffect(() => {
    let active = true;
    setImageUrl('');
    if (!layer.visible) {
      onStatus(layer.id, 'idle');
      return () => { active = false; };
    }

    onStatus(layer.id, 'loading');
    let entry = cache.current.get(cacheKey);
    if (!entry) {
      const request = layer.previewArtifact
        ? downloadArtifact(layer.resultId, layer.previewArtifact)
        : getResultImage(layer.resultId, layer.imageKey);
      entry = {
        promise: request.then(({ data }) => {
          const url = URL.createObjectURL(data);
          cache.current.set(cacheKey, { url, promise: Promise.resolve(url) });
          return url;
        }),
      };
      cache.current.set(cacheKey, entry);
    }
    entry.promise
      .then((url) => {
        if (!active) return;
        setImageUrl(url);
        onStatus(layer.id, 'loaded');
      })
      .catch(() => {
        if (active) onStatus(layer.id, 'error');
      });
    return () => { active = false; };
  }, [
    cache,
    cacheKey,
    layer.id,
    layer.imageKey,
    layer.previewArtifact,
    layer.resultId,
    layer.visible,
    onStatus,
    retryVersion,
  ]);

  if (!layer.visible || !imageUrl) return null;
  return (
    <Pane name={paneName} style={{ zIndex: 410 + layer.order }}>
      <ImageOverlay
        url={imageUrl}
        bounds={leafletBounds(layer.bounds)}
        opacity={layer.opacity}
        interactive={false}
      />
    </Pane>
  );
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function GISWorkspace({
  scenes = [],
  scenesLoading = false,
  selectedScene = null,
  recentResults = [],
  savedResults = [],
  resultsLoading = false,
  resultsError = '',
  hasMoreResults = false,
  loadingMoreResults = false,
  onRefreshResults,
  onLoadMoreResults,
  requestedResult = null,
}) {
  const [layers, setLayers] = useState([]);
  const [sceneId, setSceneId] = useState('');
  const [sceneDetails, setSceneDetails] = useState(null);
  const [sceneLoading, setSceneLoading] = useState(false);
  const [selectedResultId, setSelectedResultId] = useState('');
  const [addingResult, setAddingResult] = useState(false);
  const [selectedLayerId, setSelectedLayerId] = useState('');
  const [fitRequest, setFitRequest] = useState(null);
  const [location, setLocation] = useState(null);
  const [tileError, setTileError] = useState(false);
  const [imageStatuses, setImageStatuses] = useState({});
  const [retryVersions, setRetryVersions] = useState({});
  const [downloadingLayer, setDownloadingLayer] = useState('');
  const [workspaceError, setWorkspaceError] = useState('');
  const imageCache = useRef(new Map());

  const updateImageStatus = useCallback((id, status) => {
    setImageStatuses((previous) => previous[id] === status
      ? previous
      : { ...previous, [id]: status });
  }, []);

  useEffect(() => {
    if (selectedScene?.id) {
      setSceneId(selectedScene.id);
      setSceneDetails(selectedScene);
    }
  }, [selectedScene]);

  useEffect(() => () => {
    for (const entry of imageCache.current.values()) {
      if (entry.url) URL.revokeObjectURL(entry.url);
      else entry.promise?.then((url) => URL.revokeObjectURL(url)).catch(() => {});
    }
    imageCache.current.clear();
  }, []);

  const resultEntries = useMemo(() => {
    const recentIds = new Set(recentResults.map((item) => item.id));
    return [
      ...recentResults.map((item) => ({ ...item, recent: true })),
      ...savedResults.filter((item) => item.analysis !== 'imagery_ingestion' && !recentIds.has(item.id)),
    ];
  }, [recentResults, savedResults]);

  const visibleLayers = useMemo(() => layers.filter((layer) => layer.visible), [layers]);
  const selectedLayer = layers.find((layer) => layer.id === selectedLayerId) ?? null;
  const hasLoadingLayer = visibleLayers.some((layer) => imageStatuses[layer.id] === 'loading');

  useEffect(() => {
    if (selectedLayerId && !layers.some((layer) => layer.id === selectedLayerId)) {
      setSelectedLayerId(layers.at(-1)?.id ?? '');
    }
  }, [layers, selectedLayerId]);

  useEffect(() => {
    if (requestedResult?.id) {
      setSelectedResultId(requestedResult.id);
      addSavedResult(requestedResult.id);
    }
  }, [requestedResult?.requestId]);

  async function selectScene(id) {
    setSceneId(id);
    setSceneDetails(null);
    setWorkspaceError('');
    if (!id) return;
    const existing = id === selectedScene?.id ? selectedScene : null;
    if (existing) {
      setSceneDetails(existing);
      return;
    }
    setSceneLoading(true);
    try {
      setSceneDetails(await getImageryScene(id));
    } catch (error) {
      setWorkspaceError(error.response?.data?.detail ?? 'The selected scene could not be loaded.');
    } finally {
      setSceneLoading(false);
    }
  }

  function addLayer(layer) {
    setWorkspaceError('');
    const found = layers.find((item) => item.id === layer.id);
    if (found) {
      setSelectedLayerId(found.id);
      requestFit(found.bounds);
      return;
    }
    setLayers((previous) => [
      ...previous,
      { ...layer, visible: true, opacity: 0.8, order: previous.length },
    ]);
    setSelectedLayerId(layer.id);
    requestFit(layer.bounds);
  }

  function addSelectedScene() {
    try {
      if (!sceneDetails) throw new Error('Select a saved scene first.');
      addLayer(sourceLayer(sceneDetails));
    } catch (error) {
      setWorkspaceError(error.message);
    }
  }

  async function addSavedResult(resultId = selectedResultId) {
    if (!resultId || addingResult) return;
    setAddingResult(true);
    setWorkspaceError('');
    try {
      const recent = recentResults.find((item) => item.id === resultId);
      const summary = recent ?? await getResult(resultId);
      const result = summary?.result && typeof summary.result === 'object'
        ? summary.result
        : summary;
      const layer = resultLayer(summary, scenes);
      if (!layer.rasterArtifact && !layer.legacyDownloadKey) {
        try {
          const manifest = await getResultArtifacts(summary.id);
          const raster = manifest.artifacts?.find((item) =>
            item.artifact_type === 'raster' || /\.(tif|tiff)$/i.test(item.artifact_name),
          );
          if (raster) layer.rasterArtifact = raster.artifact_name;
        } catch (error) {
          const detail = error.response?.data?.detail;
          layer.downloadWarning = typeof detail === 'string'
            ? `Raster-download metadata is unavailable: ${detail}`
            : 'Raster-download metadata is unavailable.';
        }
      }
      addLayer(layer);
    } catch (error) {
      setWorkspaceError(
        error.response?.data?.detail
        ?? error.message
        ?? 'The selected result could not be retrieved.',
      );
    } finally {
      setAddingResult(false);
    }
  }

  function loadMoreResults() {
    onLoadMoreResults?.();
  }

  function updateLayer(id, update) {
    setLayers((previous) => previous.map((layer) => layer.id === id ? { ...layer, ...update } : layer));
  }

  function moveLayer(id, direction) {
    setLayers((previous) => {
      const index = previous.findIndex((layer) => layer.id === id);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= previous.length) return previous;
      const next = [...previous];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next.map((layer, order) => ({ ...layer, order }));
    });
  }

  function removeLayer(layer) {
    if (!window.confirm(`Remove "${layer.name}" from this map? The saved artifact will not be deleted.`)) return;
    setLayers((previous) => previous.filter((item) => item.id !== layer.id)
      .map((item, order) => ({ ...item, order })));
    setImageStatuses((previous) => {
      const next = { ...previous };
      delete next[layer.id];
      return next;
    });
  }

  function requestFit(bounds) {
    setFitRequest({ mode: 'fit', bounds: leafletBounds(bounds), version: Date.now() });
  }

  function fitVisibleLayers() {
    if (!visibleLayers.length) return;
    const west = Math.min(...visibleLayers.map((layer) => layer.bounds[0]));
    const south = Math.min(...visibleLayers.map((layer) => layer.bounds[1]));
    const east = Math.max(...visibleLayers.map((layer) => layer.bounds[2]));
    const north = Math.max(...visibleLayers.map((layer) => layer.bounds[3]));
    setFitRequest({ mode: 'fit', bounds: leafletBounds([west, south, east, north]), version: Date.now() });
  }

  async function downloadLayer(layer) {
    if (downloadingLayer) return;
    const artifactName = layer.sourceDownload ?? layer.rasterArtifact;
    if (!artifactName && !layer.legacyDownloadKey) return;
    setDownloadingLayer(layer.id);
    setWorkspaceError('');
    try {
      const response = layer.legacyDownloadKey
        ? await downloadResult(layer.resultId, layer.legacyDownloadKey)
        : await downloadArtifact(layer.resultId, artifactName);
      downloadBlob(response.data, artifactName ?? `${layer.legacyDownloadKey}.tif`);
    } catch (error) {
      setWorkspaceError(error.response?.data?.detail ?? 'The raster download failed.');
    } finally {
      setDownloadingLayer('');
    }
  }

  function retryLayer(layer) {
    const key = `${layer.resultId}:${layer.previewArtifact ?? layer.imageKey}`;
    imageCache.current.delete(key);
    setRetryVersions((previous) => ({ ...previous, [layer.id]: (previous[layer.id] ?? 0) + 1 }));
  }

  return (
    <section id="gis-workspace" className="gis-workspace" aria-label="Advanced GIS workspace">
      <div className="gis-workspace-heading">
        <div>
          <p className="eyebrow">GEOSPATIAL WORKSPACE</p>
          <h3>Map layers and scene information</h3>
        </div>
        <span className="gis-projection-label">Overlay grid · EPSG:4326</span>
      </div>
      <p className="gis-description">
        Source previews and analysis previews are served through authenticated artifact endpoints.
        Map bounds come from the preview geotransform; the source GeoTIFF is unchanged.
      </p>

      <div className="gis-add-controls">
        <label>
          <span>Saved scene</span>
          <select
            aria-label="GIS saved scene"
            value={sceneId}
            disabled={scenesLoading || !scenes.length || sceneLoading}
            onChange={(event) => selectScene(event.target.value)}
          >
            <option value="">
              {scenesLoading || sceneLoading
                ? 'Loading scene…'
                : scenes.length
                  ? 'Select a saved scene'
                  : 'No saved scenes'}
            </option>
            {scenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.metadata?.acquisition_date ?? 'Date unknown'} · {scene.metadata?.original_filename ?? scene.id}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary-button" onClick={addSelectedScene} disabled={!sceneDetails || sceneLoading}>
          Add scene preview
        </button>
        <label>
          <span>Saved or completed analysis</span>
          <select
            aria-label="GIS analysis result"
            value={selectedResultId}
            disabled={resultsLoading && !resultEntries.length}
            onChange={(event) => setSelectedResultId(event.target.value)}
          >
            <option value="">
              {resultsLoading && !resultEntries.length ? 'Loading results…' : 'Select a result'}
            </option>
            {resultEntries.map((item) => (
              <option key={item.id} value={item.id}>
                {item.recent ? 'Just completed · ' : ''}
                {readableAnalysis(item.analysis)} · {item.created_at ?? 'date unknown'}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary-button" onClick={() => addSavedResult()} disabled={!selectedResultId || addingResult}>
          {addingResult ? 'Loading result…' : 'Add result layer'}
        </button>
        {hasMoreResults && (
          <button type="button" className="gis-text-button" onClick={loadMoreResults} disabled={resultsLoading}>
            {loadingMoreResults ? 'Loading…' : 'More results'}
          </button>
        )}
      </div>
      {resultsError && (
        <div className="gis-inline-error" role="alert">
          {resultsError}
          <button type="button" onClick={onRefreshResults} disabled={!onRefreshResults || resultsLoading}>
            {resultsLoading ? 'Loading…' : 'Retry'}
          </button>
        </div>
      )}
      {workspaceError && <p className="gis-inline-error" role="alert">{workspaceError}</p>}

      <div className="gis-layout">
        <div className="gis-map-column">
          <div className="gis-map-toolbar">
            <button type="button" onClick={() => selectedLayer && requestFit(selectedLayer.bounds)} disabled={!selectedLayer}>
              Fit selected
            </button>
            <button type="button" onClick={fitVisibleLayers} disabled={!visibleLayers.length}>
              Fit visible layers
            </button>
            <button type="button" onClick={() => setFitRequest({ mode: 'reset', version: Date.now() })} disabled={!layers.length}>
              Reset view
            </button>
          </div>
          {layers.length ? (
            <div className="gis-map-frame">
              <MapContainer center={[0, 0]} zoom={2} scrollWheelZoom className="gis-leaflet-map">
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  eventHandlers={{ tileerror: () => setTileError(true) }}
                />
                <ScaleControl position="bottomleft" metric imperial={false} />
                <MapActions fitRequest={fitRequest} onLocation={setLocation} />
                {layers.map((layer) => (
                  <RasterLayer
                    key={layer.id}
                    layer={layer}
                    paneName={`gis-layer-${layer.order}`}
                    cache={imageCache}
                    status={imageStatuses[layer.id]}
                    onStatus={updateImageStatus}
                    retryVersion={retryVersions[layer.id] ?? 0}
                  />
                ))}
              </MapContainer>
              {hasLoadingLayer && <p className="gis-map-status" role="status">Preparing visible raster preview…</p>}
              {tileError && (
                <p className="gis-map-status gis-tile-warning" role="status">
                  OpenStreetMap tiles are unavailable. Raster overlays and controls remain available.
                </p>
              )}
            </div>
          ) : (
            <div className="gis-map-empty" role="status">
              <b>No map layers selected</b>
              <span>Add a saved scene preview or completed analysis result to begin.</span>
            </div>
          )}
          <div className="gis-location" aria-live="polite">
            <b>Map location</b>
            <span>
              {location
                ? `${location[0].toFixed(6)}° longitude, ${location[1].toFixed(6)}° latitude · EPSG:4326`
                : 'Click the map to inspect coordinates · pixel sampling unavailable'}
            </span>
          </div>
        </div>

        <aside className="gis-layer-panel" aria-label="Layer manager">
          <h4>Layer manager <span>{layers.length}</span></h4>
          {!layers.length ? (
            <p className="gis-empty-layers">Layers added to this map appear here.</p>
          ) : (
            <ol className="gis-layer-list">
              {[...layers].reverse().map((layer) => {
                const imageStatus = imageStatuses[layer.id] ?? 'idle';
                const topIndex = layers.length - 1;
                const order = layers.findIndex((item) => item.id === layer.id);
                return (
                  <li key={layer.id} className={selectedLayerId === layer.id ? 'selected' : ''}>
                    <div className="gis-layer-title">
                      <label>
                        <input
                          type="checkbox"
                          aria-label={`Show ${layer.name}`}
                          checked={layer.visible}
                          onChange={(event) => updateLayer(layer.id, { visible: event.target.checked })}
                        />
                        <span>{layer.name}</span>
                      </label>
                      <button type="button" aria-label={`Select ${layer.name}`} onClick={() => setSelectedLayerId(layer.id)}>Info</button>
                    </div>
                    <p className="gis-layer-kind">{layer.kind}{layer.acquisitionDate ? ` · ${layer.acquisitionDate}` : ' · date unknown'}</p>
                    <label className="gis-opacity">
                      <span>Opacity</span>
                      <input
                        aria-label={`Opacity ${layer.name}`}
                        type="range"
                        min="0"
                        max="1"
                        step="0.05"
                        value={layer.opacity}
                        onChange={(event) => updateLayer(layer.id, { opacity: Number(event.target.value) })}
                      />
                      <output>{Math.round(layer.opacity * 100)}%</output>
                    </label>
                    <div className="gis-layer-actions">
                      <button type="button" onClick={() => moveLayer(layer.id, 1)} disabled={order === topIndex}>Bring forward</button>
                      <button type="button" onClick={() => moveLayer(layer.id, -1)} disabled={order === 0}>Send backward</button>
                      <button type="button" onClick={() => removeLayer(layer)}>Remove</button>
                      {layer.rasterArtifact || layer.legacyDownloadKey || layer.sourceDownload ? (
                        <button
                          type="button"
                          disabled={Boolean(downloadingLayer)}
                          onClick={() => downloadLayer(layer)}
                        >
                          {downloadingLayer === layer.id ? 'Preparing…' : layer.sourceDownload ? 'Download source GeoTIFF' : 'Download raster'}
                        </button>
                      ) : null}
                    </div>
                    {imageStatus === 'loading' && <p className="gis-layer-status" role="status">Loading private preview…</p>}
                    {imageStatus === 'error' && (
                      <p className="gis-layer-status gis-error" role="alert">
                        Preview unavailable.
                        <button type="button" onClick={() => retryLayer(layer)}>Retry</button>
                      </p>
                    )}
                    {selectedLayerId === layer.id && (
                      <dl className="gis-layer-metadata">
                        <div><dt>Source scene</dt><dd>{layer.sceneName ?? layer.sceneId ?? 'Not reported'}</dd></div>
                        <div><dt>Acquisition</dt><dd>{layer.acquisitionDate ?? 'Unknown'}</dd></div>
                        <div><dt>Source CRS</dt><dd>{layer.sourceCrs}</dd></div>
                        <div><dt>Overlay CRS</dt><dd>{layer.overlayCrs}</dd></div>
                        <div><dt>Raster dimensions</dt><dd>{layer.dimensions}</dd></div>
                        <div><dt>Resolution</dt><dd>{layer.resolution?.join(' × ') ?? 'Not reported'}</dd></div>
                        <div><dt>Preview bounds WGS84</dt><dd>{layer.bounds.map((value) => value.toFixed(5)).join(', ')}</dd></div>
                        {layer.sourceBounds && (
                          <div><dt>Source raster bounds</dt><dd>{layer.sourceBounds.map((value) => value.toFixed(5)).join(', ')}</dd></div>
                        )}
                        {layer.bands && <div><dt>Bands</dt><dd>{layer.bands.map((band) => band.code ?? band.name ?? band.index).join(', ')}</dd></div>}
                        {layer.interpretation && <div><dt>Notes</dt><dd>{layer.interpretation}</dd></div>}
                        {layer.persistence && <div><dt>Persistence</dt><dd>{layer.persistence === 'cloud-private' ? 'Private cloud' : 'Local-only or unknown'}</dd></div>}
                      </dl>
                    )}
                    {layer.legend?.length ? (
                      <ul className="gis-legend">
                        {layer.legend.map((item, index) => (
                          <li key={item.value ?? item.range ?? item.label ?? index}>
                            <i style={{ backgroundColor: item.color ?? '#738581' }} />
                            <span>{item.label ?? item.value ?? 'Class'}</span>
                            {item.range && <small>{item.range}</small>}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="gis-layer-kind">
                        {layer.kind === 'Source imagery preview'
                          ? 'RGB/grayscale preview · nodata transparent'
                          : 'No quantitative legend was supplied for this result.'}
                      </p>
                    )}
                    {!layer.sourceDownload && !layer.rasterArtifact && !layer.legacyDownloadKey && (
                      <p className="gis-layer-kind">
                        {layer.downloadWarning ?? 'No registered georeferenced raster download is available for this result.'}
                      </p>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </aside>
      </div>
      <p className="gis-limitations">
        Layers are georeferenced PNG previews, not the source raster grid. Coordinates report
        longitude then latitude in WGS84. Click sampling is unavailable; cluster labels and
        thresholded changes are not verified land-cover classes or confirmed events.
      </p>
    </section>
  );
}
