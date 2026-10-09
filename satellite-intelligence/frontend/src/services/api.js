import axios from 'axios';

export function normalizeApiRootUrl(value) {
  return value?.trim().replace(/\/+$/, '').replace(/\/api$/i, '') || '';
}

const configuredApiRootUrl = normalizeApiRootUrl(import.meta.env.VITE_API_URL);
export const apiRootUrl = configuredApiRootUrl || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
export const apiBaseUrl = `${apiRootUrl}/api`;
export const api = axios.create({ baseURL: apiBaseUrl, timeout: 120000 });

export async function getHealth() {
  if (!apiRootUrl) {
    throw new Error('VITE_API_URL is required for production builds.');
  }
  const { data } = await api.get('/health');
  if (data?.status !== 'ok' || data?.service !== 'satellite-intelligence-api') {
    throw new Error('The API health endpoint returned an unexpected response.');
  }
  return data;
}

export async function getDataset() {
  return (await api.get('/dataset')).data;
}

export async function uploadBand(period, file, onUploadProgress) {
  const body = new FormData();
  body.append('file', file);
  return (await api.post(`/dataset/${encodeURIComponent(period)}/upload`, body, {
    onUploadProgress,
  })).data;
}

export async function runNDVI() {
  return (await api.post('/analyze/ndvi')).data;
}

export async function runNDWI() {
  return (await api.post('/analyze/ndwi')).data;
}

export async function runNDBI() {
  return (await api.post('/analyze/ndbi')).data;
}

export async function runLandcover() {
  return (await api.post('/analyze/landcover')).data;
}

export async function runChangeDetection() {
  return (await api.post('/analyze/change')).data;
}

export async function runAllAnalysis() {
  return (await api.post('/analyze/all')).data;
}

export async function getSatelliteStatus() {
  return (await api.get('/satellite/status')).data;
}

export async function searchSatellite(payload) {
  return (await api.post('/satellite/search', payload)).data;
}

export async function downloadSatellite(payload) {
  return (await api.post('/satellite/download', payload)).data;
}

export async function listSatelliteProducts() {
  return (await api.get('/satellite/products')).data;
}

export async function getResults() {
  return (await api.get('/results')).data;
}

export async function getMetadata() {
  return (await api.get('/metadata')).data;
}

export async function getDataQuality() {
  return (await api.get('/data-quality')).data;
}

export async function getModelStatus() {
  return (await api.get('/model/status')).data;
}

export async function getGeoAIStatus() {
  return (await api.get('/geoai/status')).data;
}

export async function runGeoAISpatialAnalysis(payload) {
  return (await api.post('/geoai/spatial-analysis', payload)).data;
}

export async function runGeoAIVegetationForecast(payload) {
  return (await api.post('/geoai/vegetation-forecast', payload)).data;
}

export async function runGeoAILandCoverTransitions(payload) {
  return (await api.post('/geoai/land-cover-transitions', payload)).data;
}

export async function getGeoAIModelEvaluation() {
  return (await api.get('/geoai/model-evaluation')).data;
}

export async function getGeoAIRiskIndicators() {
  return (await api.get('/geoai/risk-indicators')).data;
}

export async function getGeoAIHistory() {
  return (await api.get('/geoai/history')).data;
}

export async function getResult(resultId) {
  return (await api.get(`/results/${encodeURIComponent(resultId)}`)).data;
}

export async function getVisualizationUrl(kind) {
  return `${apiBaseUrl}/visualization/${encodeURIComponent(kind)}`;
}

export async function downloadResult(resultId, fileKey) {
  return api.get(
    `/results/${encodeURIComponent(resultId)}/download/${encodeURIComponent(fileKey)}`,
    { responseType: 'blob' },
  );
}

export function getResultImageUrl(resultId, analysisKey) {
  return `${apiBaseUrl}/results/${encodeURIComponent(resultId)}/image/${encodeURIComponent(analysisKey)}`;
}

export function getApiResourceUrl(path) {
  return new URL(path, `${apiRootUrl}/`).toString();
}
