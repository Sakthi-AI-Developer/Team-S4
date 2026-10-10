import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AnalysisHistory from './AnalysisHistory';

const api = vi.hoisted(() => ({
  getAnalysisRecord: vi.fn(),
  getResultArtifacts: vi.fn(),
  downloadArtifact: vi.fn(),
}));

vi.mock('../services/api', () => api);

const results = [
  {
    id: 'a'.repeat(32),
    analysis: 'NDVI',
    status: 'completed',
    created_at: '2026-01-01T12:00:00Z',
    history: {
      method: 'NDVI',
      source_scenes: [{ role: 'Scene', id: 'scene-1', acquisition_date: null }],
      parameters: { threshold: 0.2 },
      statistics: { Summary: { mean: 0.42, valid_pixels: 16 } },
    },
  },
  { id: 'b'.repeat(32), analysis: 'NDWI', status: 'failed', created_at: null, history: {} },
  { id: 'c'.repeat(32), analysis: 'NDVI', status: 'running', history: {} },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.getAnalysisRecord.mockResolvedValue({ id: results[0].id, status: 'completed' });
  api.getResultArtifacts.mockResolvedValue({
    artifacts: [{ artifact_name: 'ndvi.tif', artifact_type: 'raster', size_bytes: 128 }],
  });
});

describe('analysis history and report', () => {
  it('filters by status and type and tolerates missing metadata', () => {
    render(<AnalysisHistory results={results} />);
    expect(screen.getByText(/Acquired: Unknown/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter analysis history by status'), {
      target: { value: 'failed' },
    });
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(within(screen.getByRole('article')).getByText('NDWI')).toBeInTheDocument();
    expect(screen.queryByText('c'.repeat(32))).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter analysis history by status'), {
      target: { value: 'all' },
    });
    fireEvent.change(screen.getByLabelText('Filter analysis history by analysis type'), {
      target: { value: 'NDWI' },
    });
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.getByText(/Created: Not recorded/)).toBeInTheDocument();
  });

  it('loads owner-scoped detail and artifacts only when a report is opened', async () => {
    const onViewInGis = vi.fn();
    render(<AnalysisHistory results={results} onViewInGis={onViewInGis} />);
    expect(api.getAnalysisRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Open report' })[0]);

    expect(await screen.findByText('0.42')).toBeInTheDocument();
    expect(screen.getByText(/not, by themselves, a confirmed ecological condition/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Download ndvi.tif/ })).toBeInTheDocument();
    expect(api.getAnalysisRecord).toHaveBeenCalledWith(results[0].id);
    expect(api.getResultArtifacts).toHaveBeenCalledWith(results[0].id);
    fireEvent.click(screen.getByRole('button', { name: 'View completed result in GIS' }));
    expect(onViewInGis).toHaveBeenCalledWith(results[0].id);
  });

  it('shows empty, loading failure and retry states', async () => {
    const { rerender } = render(<AnalysisHistory results={[]} />);
    expect(screen.getByText('No saved analyses are available yet.')).toBeInTheDocument();

    api.getAnalysisRecord.mockRejectedValueOnce(new Error('offline'));
    rerender(<AnalysisHistory results={results} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Open report' })[0]);
    expect(await screen.findByRole('alert')).toHaveTextContent('Analysis details could not be loaded. Please retry.');
    api.getAnalysisRecord.mockResolvedValue({ id: results[0].id, status: 'completed' });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Computed statistics and metrics')).toBeInTheDocument();
  });

  it('reports unavailable artifacts while still rendering report details', async () => {
    api.getResultArtifacts.mockRejectedValue(new Error('unavailable'));
    render(<AnalysisHistory results={results} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Open report' })[0]);
    expect(await screen.findByRole('heading', { name: 'NDVI' })).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('Saved artifact metadata is unavailable');
  });
});
