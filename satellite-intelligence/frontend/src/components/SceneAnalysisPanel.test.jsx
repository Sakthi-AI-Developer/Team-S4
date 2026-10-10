import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SceneAnalysisPanel from './SceneAnalysisPanel';
import {
  analyzeImageryScene,
  downloadArtifact,
  getImageryAnalysisTypes,
} from '../services/api';

vi.mock('./MapView', () => ({
  default: ({ layerName }) => <div data-testid="scene-analysis-map">{layerName}</div>,
}));

vi.mock('../services/api', () => ({
  analyzeImageryScene: vi.fn(),
  downloadArtifact: vi.fn(),
  getImageryAnalysis: vi.fn(),
  getImageryAnalysisTypes: vi.fn(),
}));

const metadata = {
  band_count: 4,
  bands: [
    { index: 1, code: 'B04', name: 'Red' },
    { index: 2, code: 'B08', name: 'Near infrared' },
    { index: 3, code: 'B03', name: 'Green' },
    { index: 4, code: 'B11', name: 'Short-wave infrared' },
  ],
};

const ndvi = {
  id: 'ndvi',
  name: 'Normalized Difference Vegetation Index (NDVI)',
  roles: { red: 'Red', nir: 'Near infrared' },
  detected_bands: { red: 1, nir: 2 },
  interpretation_note: 'Illustrative ranges only.',
};

const kmeans = {
  id: 'kmeans',
  name: 'Unsupervised multispectral K-means',
  roles: {},
  detected_bands: {},
  interpretation_note: 'Clusters are spectral groups, not verified land-cover classes.',
};

const result = {
  success: true,
  id: 'analysis-1',
  analysis_type: 'ndvi',
  status: 'completed',
  result: {
    formula: '(NIR - Red) / (NIR + Red)',
    bounds: [18, 12, 19, 13],
    preview: { artifact_name: 'preview.png' },
    raster: { artifact_name: 'ndvi.tif' },
    statistics: {
      valid_pixels: 12,
      min: -0.1,
      max: 0.8,
      mean: 0.3,
      median: 0.31,
    },
    legend: [{ range: '< 0', label: 'Lower relative vegetation response', color: '#8c6a9e' }],
    interpretation_note: 'Illustrative ranges only.',
  },
  artifacts: [{ artifact_name: 'ndvi.tif' }],
};

describe('SceneAnalysisPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getImageryAnalysisTypes.mockResolvedValue({ analysis_types: [ndvi, kmeans] });
    analyzeImageryScene.mockResolvedValue(result);
    downloadArtifact.mockResolvedValue({ data: new Blob(['raster']) });
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:result'),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  it('submits a supported analysis and renders the real response, map, statistics, and download', async () => {
    render(<SceneAnalysisPanel sceneId="scene-1" metadata={metadata} />);

    expect(await screen.findByRole('button', { name: 'Run scene analysis' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Run scene analysis' }));

    expect(await screen.findByTestId('scene-analysis-map')).toHaveTextContent(ndvi.name);
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('0.3000')).toBeInTheDocument();
    expect(screen.getByText('Lower relative vegetation response')).toBeInTheDocument();
    expect(analyzeImageryScene).toHaveBeenCalledWith('scene-1', {
      analysis_type: 'ndvi',
      band_mapping: { red: 1, nir: 2 },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Download ndvi.tif' }));
    await waitFor(() => expect(downloadArtifact).toHaveBeenCalledWith('analysis-1', 'ndvi.tif'));
  });

  it('requires a user-selected band when an automatic band identity is missing', async () => {
    getImageryAnalysisTypes.mockResolvedValue({
      analysis_types: [{ ...ndvi, detected_bands: { red: 1, nir: null } }],
    });
    render(<SceneAnalysisPanel sceneId="scene-2" metadata={metadata} />);

    const submit = await screen.findByRole('button', { name: 'Run scene analysis' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Near infrared band'), { target: { value: '2' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(analyzeImageryScene).toHaveBeenCalledWith('scene-2', {
      analysis_type: 'ndvi',
      band_mapping: { red: 1, nir: 2 },
    }));
  });

  it('allows neutral-label K-means feature and cluster configuration', async () => {
    render(<SceneAnalysisPanel sceneId="scene-3" metadata={metadata} />);
    await screen.findByRole('button', { name: 'Run scene analysis' });
    fireEvent.change(screen.getByLabelText('Scene analysis type'), {
      target: { value: 'kmeans' },
    });
    fireEvent.change(screen.getByLabelText('Number of spectral clusters'), {
      target: { value: '4' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run scene analysis' }));

    await waitFor(() => expect(analyzeImageryScene).toHaveBeenCalledWith('scene-3', {
      analysis_type: 'kmeans',
      feature_bands: [1, 2, 3],
      cluster_count: 4,
      random_seed: 42,
    }));
  });

  it('displays backend processing errors without showing a fabricated result', async () => {
    analyzeImageryScene.mockRejectedValue({
      response: { status: 422, data: { detail: 'NDVI requires an identifiable NIR band.' } },
    });
    render(<SceneAnalysisPanel sceneId="scene-4" metadata={metadata} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run scene analysis' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'NDVI requires an identifiable NIR band.',
    );
    expect(screen.queryByTestId('scene-analysis-map')).not.toBeInTheDocument();
  });
});
