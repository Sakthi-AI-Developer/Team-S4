import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, apiBaseUrl, downloadResult, getDataset, getHealth, getResultImageUrl, getResults, normalizeApiRootUrl, runAllAnalysis, runChangeDetection, runLandcover, runNDBI, runNDVI, runNDWI, uploadBand } from './api';

describe('API service', () => {
  afterEach(() => vi.restoreAllMocks());

  it('calls the backend health and dataset routes', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      data: { status: 'ok', service: 'satellite-intelligence-api', provider: 'local', configured: false },
    });
    await expect(getHealth()).resolves.toMatchObject({ status: 'ok', service: 'satellite-intelligence-api' });
    await getDataset();
    expect(get).toHaveBeenNthCalledWith(1, '/health');
    expect(get).toHaveBeenNthCalledWith(2, '/dataset');
  });

  it('normalizes API origins with or without a trailing /api path', () => {
    expect(normalizeApiRootUrl('https://api.example.test/')).toBe('https://api.example.test');
    expect(normalizeApiRootUrl('https://api.example.test/api/')).toBe('https://api.example.test');
    expect(apiBaseUrl).toMatch(/\/api$/);
    expect(apiBaseUrl).not.toMatch(/\/api\/api$/);
  });

  it('rejects an invalid health response instead of marking the API online', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: { status: 'degraded', service: 'satellite-intelligence-api' } });
    await expect(getHealth()).rejects.toThrow('health endpoint returned an unexpected response');
  });

  it('rejects invalid JSON health payloads', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: 'not JSON' });
    await expect(getHealth()).rejects.toThrow('health endpoint returned an unexpected response');
  });

  it('preserves HTTP and timeout errors for the UI to report accurately', async () => {
    const get = vi.spyOn(api, 'get');
    get.mockRejectedValueOnce({ response: { status: 503 } });
    await expect(getHealth()).rejects.toMatchObject({ response: { status: 503 } });

    get.mockRejectedValueOnce({ code: 'ECONNABORTED' });
    await expect(getHealth()).rejects.toMatchObject({ code: 'ECONNABORTED' });
  });

  it('uses the actual analysis and results endpoints', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: {} });
    await runNDVI();
    await runNDWI();
    await runNDBI();
    await runLandcover();
    await runChangeDetection();
    await runAllAnalysis();
    await getResults();
    await downloadResult('id-1', 'ndvi');
    expect(post.mock.calls.map(([path]) => path)).toEqual([
      '/analyze/ndvi',
      '/analyze/ndwi',
      '/analyze/ndbi',
      '/analyze/landcover',
      '/analyze/change',
      '/analyze/all',
    ]);
    expect(get).toHaveBeenLastCalledWith('/results/id-1/download/ndvi', { responseType: 'blob' });
  });

  it('builds a URL for the requested result layer', () => {
    expect(getResultImageUrl('123', 'ndvi')).toContain('/api/results/123/image/ndvi');
  });

  it('uploads GeoTIFF bands to the selected local dataset period', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { success: true } });
    const file = new File(['raster-data'], 'S2_B04.tif', { type: 'image/tiff' });
    await uploadBand('current', file);
    const [path, body, options] = post.mock.calls[0];
    expect(path).toBe('/dataset/current/upload');
    expect(body.get('file')).toBe(file);
    expect(options.onUploadProgress).toBeUndefined();
  });
});
