import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const service = vi.hoisted(() => ({
  getDataset: vi.fn(),
  getHealth: vi.fn(),
  getResults: vi.fn(),
  getSatelliteStatus: vi.fn(),
  getGeoAIStatus: vi.fn(),
  getGeoAIHistory: vi.fn(),
  runGeoAISpatialAnalysis: vi.fn(),
  runGeoAIVegetationForecast: vi.fn(),
  runGeoAILandCoverTransitions: vi.fn(),
  getGeoAIModelEvaluation: vi.fn(),
  getGeoAIRiskIndicators: vi.fn(),
  runAllAnalysis: vi.fn(),
  runNDVI: vi.fn(),
  runNDWI: vi.fn(),
  runNDBI: vi.fn(),
  runLandcover: vi.fn(),
  runChangeDetection: vi.fn(),
  getResult: vi.fn(),
  uploadBand: vi.fn(),
}));

vi.mock('./services/api', () => ({
  ...service,
  apiRootUrl: 'http://127.0.0.1:8000',
}));
vi.mock('./components/MapView', () => ({
  default: () => <div data-testid="map">Map</div>,
}));

const emptyDataset = {
  current: { available: false, bands: [], errors: [] },
  historical: { available: false, bands: [], errors: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  service.getHealth.mockResolvedValue({ status: 'ok' });
  service.getDataset.mockResolvedValue(emptyDataset);
  service.getResults.mockResolvedValue({ results: [] });
  service.getSatelliteStatus.mockResolvedValue({ configured: false, provider: 'local', message: 'Stage-A local dataset mode is active.' });
  service.getGeoAIStatus.mockResolvedValue({ status: 'ok', available: true, components: ['spatial_analysis', 'forecasting', 'risk'] });
  service.getGeoAIHistory.mockResolvedValue({ success: true, status: 'ok', count: 2, mean_ndvi: 0.42, latest_ndvi: 0.44, start_date: '2024-01-01', end_date: '2024-02-01' });
  service.runGeoAISpatialAnalysis.mockResolvedValue({ success: true, status: 'ok', ndvi: { mean: 0.3 }, persistent_change: { mean_ndvi_delta: -0.02 } });
  service.runGeoAIVegetationForecast.mockResolvedValue({ success: true, status: 'ok', forecast_values: [0.4, 0.41], forecast_dates: ['2024-03-01', '2024-03-02'], historical_trend: { slope_per_day: 0.0001 }, prediction_interval: { support: true }, limitations: 'Baseline forecast only.' });
  service.runGeoAILandCoverTransitions.mockResolvedValue({ success: true, status: 'ok', valid_pixel_count: 100, transitions: [{ source_label: 'Vegetation', destination_label: 'Bare ground', pixel_count: 10 }] });
  service.getGeoAIModelEvaluation.mockResolvedValue({ success: true, status: 'ok', rmse: 0.03, mean_absolute_error: 0.02 });
  service.getGeoAIRiskIndicators.mockResolvedValue({ success: true, status: 'ok', count: 1, indicators: [{ severity: 'medium', indicator_name: 'Potential persistent NDVI decline' }] });
  service.uploadBand.mockResolvedValue({ success: true });
  service.runAllAnalysis.mockResolvedValue({ id: 'result-1' });
});

describe('dashboard', () => {
  it('renders real backend status and the empty-dataset instructions', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getAllByText('System Online').length).toBeGreaterThan(0));
    expect(screen.queryByText(/Backend is unavailable|health check failed/)).not.toBeInTheDocument();
    expect(screen.getAllByText('No satellite dataset available.').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/backend\/data\/current\//).length).toBeGreaterThan(0);
    expect(screen.getByTestId('map')).toBeInTheDocument();
  });

  it('shows backend offline state without crashing', async () => {
    service.getHealth.mockRejectedValue(new Error('offline'));
    service.getDataset.mockRejectedValue(new Error('offline'));
    render(<App />);
    expect((await screen.findAllByText('Backend Offline')).length).toBeGreaterThan(0);
    expect(screen.getByText(/Check the configured API URL/)).toBeInTheDocument();
  });

  it('reports HTTP health failures separately from connection failures', async () => {
    service.getHealth.mockRejectedValue({ response: { status: 503 } });
    service.getDataset.mockRejectedValue(new Error('offline'));
    render(<App />);
    expect(await screen.findByText('Backend health check failed with HTTP 503.')).toBeInTheDocument();
  });

  it('disables duplicate complete-analysis requests during loading', async () => {
    service.getDataset.mockResolvedValue({
      current: { available: true, bands: [{ code: 'B04' }, { code: 'B08' }] },
      historical: { available: false, bands: [] },
    });
    let finishRequest;
    service.runAllAnalysis.mockImplementation(() => new Promise((resolve) => { finishRequest = resolve; }));
    render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Run complete analysis/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /Run complete analysis/ }));
    expect(await screen.findByRole('button', { name: /Processing imagery/ })).toBeDisabled();
    finishRequest({ id: 'result-1', ndvi: { success: false, message: 'Missing bands' } });
    await waitFor(() => expect(screen.getByText(/Some analyses were unavailable/)).toBeInTheDocument());
  });

  it('uploads selected local files to the current dataset and refreshes availability', async () => {
    service.getDataset
      .mockResolvedValueOnce(emptyDataset)
      .mockResolvedValueOnce({
        current: { available: true, bands: [{ code: 'B04' }] },
        historical: { available: false, bands: [] },
      });
    render(<App />);
    const file = new File(['valid-geotiff-payload'], 'Sentinel_B04.tif', { type: 'image/tiff' });
    fireEvent.change(screen.getByLabelText('Upload Current dataset GeoTIFF bands'), {
      target: { files: [file] },
    });
    await waitFor(() => expect(service.uploadBand).toHaveBeenCalledWith('current', file, expect.any(Function)));
    await waitFor(() => expect(screen.getByText(/1 GeoTIFF band was added to the current dataset/)).toBeInTheDocument());
    expect(service.getDataset).toHaveBeenCalledTimes(2);
  });
});
