import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChangeDetectionPanel from './ChangeDetectionPanel';
import {
  compareImageryScenes,
  downloadArtifact,
  getImageryAnalysis,
} from '../services/api';

vi.mock('./MapView', () => ({
  default: ({ layerName }) => <div data-testid="change-detection-map">{layerName}</div>,
}));

vi.mock('../services/api', () => ({
  compareImageryScenes: vi.fn(),
  downloadArtifact: vi.fn(),
  getImageryAnalysis: vi.fn(),
}));

const scenes = [
  {
    id: 'a'.repeat(32),
    metadata: {
      original_filename: 'baseline.tif',
      acquisition_date: '2024-01-01',
      platform: 'Sentinel-2A',
      sensor: 'MSI',
      bands: [
        { index: 1, code: 'B04' },
        { index: 2, code: 'B08' },
      ],
    },
  },
  {
    id: 'b'.repeat(32),
    metadata: {
      original_filename: 'comparison.tif',
      acquisition_date: '2024-02-01',
      platform: 'Sentinel-2B',
      sensor: 'MSI',
      bands: [
        { index: 1, code: 'B04' },
        { index: 2, code: 'B08' },
      ],
    },
  },
];

const completedAnalysis = {
  id: 'analysis-1',
  status: 'completed',
  baseline: { acquisition_date: '2024-01-01' },
  comparison: { acquisition_date: '2024-02-01' },
  result: {
    method: 'Windowed NDVI difference on the baseline grid',
    threshold: 0.1,
    threshold_units: 'absolute NDVI difference',
    bounds: [18, 12, 19, 13],
    preview: { artifact_name: 'preview.png' },
    raster: { artifact_name: 'ndvi-difference.tif' },
    change_mask: { artifact_name: 'change-mask.tif' },
    statistics: { min: -0.3, max: 0.4, mean: 0.1, median: 0.08 },
    valid_comparison_pixels: 10,
    total_target_pixels: 12,
    valid_pixel_percentage: 83.3,
    changed_pixels: 4,
    change_percentage_of_valid_pixels: 40,
    positive_change_pixels: 3,
    negative_change_pixels: 1,
    unchanged_pixels: 6,
    valid_comparison_area_square_metres: 10000,
    changed_area_square_metres: 4000,
    legend: [
      { value: 1, label: 'Candidate negative NDVI change', color: '#c43c39' },
      { value: 0, label: 'Below threshold / little or no change', color: '#f2e8b6' },
      { value: 2, label: 'Candidate positive NDVI change', color: '#2a9855' },
    ],
    alignment: {
      target: 'baseline scene grid',
      resampling: 'bilinear',
      comparison_was_reprojected_or_resampled: true,
    },
    nodata_policy: 'Only common valid pixels contribute.',
    limitations: 'Candidate changes are not confirmed environmental events.',
  },
};

describe('ChangeDetectionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    compareImageryScenes.mockResolvedValue(completedAnalysis);
    getImageryAnalysis.mockResolvedValue(completedAnalysis);
    downloadArtifact.mockResolvedValue({ data: new Blob(['raster']) });
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:change-result'),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  it('submits two dated scenes and renders response statistics, map, legend and downloads', async () => {
    render(<ChangeDetectionPanel scenes={scenes} />);

    fireEvent.change(screen.getByLabelText('Baseline scene'), {
      target: { value: scenes[0].id },
    });
    fireEvent.change(screen.getByLabelText('Comparison scene'), {
      target: { value: scenes[1].id },
    });
    const submit = screen.getByRole('button', { name: 'Run change detection' });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    expect(await screen.findByTestId('change-detection-map')).toHaveTextContent(
      'Candidate NDVI change',
    );
    expect(screen.getByText('10 / 12 (83.3%)')).toBeInTheDocument();
    expect(screen.getByText('0.1000 / 0.0800')).toBeInTheDocument();
    expect(screen.getByText('Candidate negative NDVI change')).toBeInTheDocument();
    expect(compareImageryScenes).toHaveBeenCalledWith({
      baseline_scene_id: scenes[0].id,
      comparison_scene_id: scenes[1].id,
      baseline_band_mapping: { red: 1, nir: 2 },
      comparison_band_mapping: { red: 1, nir: 2 },
      threshold: 0.1,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Download NDVI difference GeoTIFF' }));
    await waitFor(() => expect(downloadArtifact).toHaveBeenCalledWith(
      'analysis-1',
      'ndvi-difference.tif',
    ));
  });

  it('blocks missing acquisition dates before submission', () => {
    const missingDateScenes = [
      scenes[0],
      { ...scenes[1], metadata: { ...scenes[1].metadata, acquisition_date: null } },
    ];
    render(<ChangeDetectionPanel scenes={missingDateScenes} />);
    fireEvent.change(screen.getByLabelText('Baseline scene'), {
      target: { value: missingDateScenes[0].id },
    });
    fireEvent.change(screen.getByLabelText('Comparison scene'), {
      target: { value: missingDateScenes[1].id },
    });
    expect(screen.getByRole('button', { name: 'Run change detection' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('valid dates');
    expect(compareImageryScenes).not.toHaveBeenCalled();
  });

  it('requires explicit band choices when metadata labels are ambiguous', () => {
    const ambiguousScenes = [
      {
        ...scenes[0],
        metadata: {
          ...scenes[0].metadata,
          bands: [
            { index: 1, code: 'B04' },
            { index: 2, code: 'B04' },
            { index: 3, code: 'B08' },
          ],
        },
      },
      scenes[1],
    ];
    render(<ChangeDetectionPanel scenes={ambiguousScenes} />);
    fireEvent.change(screen.getByLabelText('Baseline scene'), {
      target: { value: ambiguousScenes[0].id },
    });
    fireEvent.change(screen.getByLabelText('Comparison scene'), {
      target: { value: ambiguousScenes[1].id },
    });

    const submit = screen.getByRole('button', { name: 'Run change detection' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Baseline red band'), {
      target: { value: '2' },
    });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(compareImageryScenes).toHaveBeenCalledWith({
      baseline_scene_id: ambiguousScenes[0].id,
      comparison_scene_id: ambiguousScenes[1].id,
      baseline_band_mapping: { red: 2, nir: 3 },
      comparison_band_mapping: { red: 1, nir: 2 },
      threshold: 0.1,
    });
  });

  it('reports loading and empty scene states without offering a comparison', () => {
    const { rerender } = render(<ChangeDetectionPanel scenes={[]} loading />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved scenes');
    expect(screen.queryByRole('button', { name: 'Run change detection' })).not.toBeInTheDocument();

    rerender(<ChangeDetectionPanel scenes={[]} />);
    expect(screen.getByText('Upload or save two dated imagery scenes to begin.')).toBeInTheDocument();

    rerender(<ChangeDetectionPanel scenes={[scenes[0]]} />);
    expect(screen.getByRole('status')).toHaveTextContent('Save a second scene');
  });

  it('shows backend incompatibility errors without displaying a result', async () => {
    compareImageryScenes.mockRejectedValue({
      response: { status: 422, data: { detail: 'The comparison scene date must be later.' } },
    });
    render(<ChangeDetectionPanel scenes={scenes} />);
    fireEvent.change(screen.getByLabelText('Baseline scene'), {
      target: { value: scenes[0].id },
    });
    fireEvent.change(screen.getByLabelText('Comparison scene'), {
      target: { value: scenes[1].id },
    });
    fireEvent.submit(screen.getByRole('button', { name: 'Run change detection' }).closest('form'));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The comparison scene date must be later.',
    );
    expect(screen.queryByTestId('change-detection-map')).not.toBeInTheDocument();
  });
});
