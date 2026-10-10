import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import GISWorkspace from './GISWorkspace';
import {
  downloadArtifact,
  downloadResult,
  getImageryScene,
  getResult,
  getResultArtifacts,
  getResultImage,
} from '../services/api';

vi.mock('react-leaflet', () => ({
  ImageOverlay: ({ bounds, opacity, url }) => (
    <div
      data-testid="raster-overlay"
      data-bounds={JSON.stringify(bounds)}
      data-opacity={opacity}
      data-url={url}
    />
  ),
  MapContainer: ({ children }) => (
    <div data-testid="leaflet-map">
      <button
        type="button"
        aria-label="Set map click location"
        onClick={() => globalThis.__gisMapEvents?.click({
          latlng: { lng: 18.1234567, lat: 13.9876543 },
        })}
      />
      {children}
    </div>
  ),
  Pane: ({ children }) => <div>{children}</div>,
  ScaleControl: () => null,
  TileLayer: ({ eventHandlers }) => (
    <button
      type="button"
      data-testid="tile-layer"
      onClick={() => eventHandlers?.tileerror?.()}
    />
  ),
  useMap: () => ({ fitBounds: vi.fn(), setView: vi.fn() }),
  useMapEvents: (handlers) => {
    globalThis.__gisMapEvents = handlers;
    return null;
  },
}));

vi.mock('../services/api', () => ({
  downloadArtifact: vi.fn(),
  downloadResult: vi.fn(),
  getImageryScene: vi.fn(),
  getResult: vi.fn(),
  getResultArtifacts: vi.fn(),
  getResultImage: vi.fn(),
}));

const scene = {
  id: 'scene-1',
  persistence: { mode: 'cloud-private' },
  metadata: {
    original_filename: 'scene.tif',
    acquisition_date: '2024-02-03',
    crs: 'EPSG:3857',
    width: 8,
    height: 6,
    resolution: [10, 10],
    bands: [{ index: 1, code: 'B04' }],
    preview: {
      crs: 'EPSG:4326',
      bounds_wgs84: [18, 12.94, 18.08, 13],
      description: 'RGB preview.',
    },
  },
};

const savedResult = {
  id: 'result-1',
  analysis: 'scene_ndvi',
  created_at: '2024-02-04',
  scene: { id: 'scene-1', filename: 'scene.tif', acquisition_date: '2024-02-03' },
  result: {
    bounds: [18, 12.94, 18.08, 13],
    preview: { artifact_name: 'preview.png' },
    raster: { artifact_name: 'ndvi.tif', crs: 'EPSG:3857', width: 8, height: 6 },
    legend: [{ label: 'Low', color: '#aa5555', range: '−1 to 0' }],
    interpretation_note: 'Index values are not a validated land-cover classification.',
  },
};

describe('GISWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.__gisMapEvents = null;
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:raster-preview'),
      revokeObjectURL: vi.fn(),
    });
    vi.stubGlobal('confirm', vi.fn(() => true));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    getImageryScene.mockResolvedValue(scene);
    getResult.mockResolvedValue(savedResult);
    getResultArtifacts.mockResolvedValue({ artifacts: [] });
    downloadArtifact.mockResolvedValue({ data: new Blob(['preview']) });
    downloadResult.mockResolvedValue({ data: new Blob(['raster']) });
    getResultImage.mockResolvedValue({ data: new Blob(['legacy-preview']) });
  });

  it('adds a saved analysis using its preview bounds and secure artifact API', async () => {
    const onLoadMoreResults = vi.fn();
    render(
      <GISWorkspace
        scenes={[scene]}
        savedResults={[savedResult]}
        hasMoreResults
        onLoadMoreResults={onLoadMoreResults}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More results' }));
    expect(onLoadMoreResults).toHaveBeenCalledOnce();
    fireEvent.change(await screen.findByLabelText('GIS analysis result'), {
      target: { value: 'result-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add result layer' }));

    const overlay = await screen.findByTestId('raster-overlay');
    expect(getResult).toHaveBeenCalledWith('result-1');
    expect(downloadArtifact).toHaveBeenCalledWith('result-1', 'preview.png');
    expect(overlay).toHaveAttribute(
      'data-bounds',
      JSON.stringify([[12.94, 18], [13, 18.08]]),
    );
    expect(screen.getByText('EPSG:3857')).toBeInTheDocument();
    expect(screen.getByText('Overlay CRS')).toBeInTheDocument();
    expect(screen.getByText(/Click the map to inspect coordinates/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Set map click location'));
    expect(screen.getByText(/18\.123457° longitude, 13\.987654° latitude · EPSG:4326/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download raster' }));
    await waitFor(() => expect(downloadArtifact).toHaveBeenCalledWith('result-1', 'ndvi.tif'));
  });

  it('adds a history-requested result through the persisted summary endpoint', async () => {
    render(
      <GISWorkspace
        scenes={[scene]}
        savedResults={[{ id: 'result-1', analysis: 'scene_ndvi' }]}
        requestedResult={{ id: 'result-1', requestId: 1 }}
      />,
    );

    expect(await screen.findByTestId('raster-overlay')).toBeInTheDocument();
    expect(getResult).toHaveBeenCalledWith('result-1');
    expect(downloadArtifact).toHaveBeenCalledWith('result-1', 'preview.png');
  });

  it('adds source previews, controls visibility and opacity, reorders, and removes layers', async () => {
    render(<GISWorkspace scenes={[scene]} savedResults={[savedResult]} />);
    fireEvent.change(screen.getByLabelText('GIS saved scene'), {
      target: { value: 'scene-1' },
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add scene preview' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Add scene preview' }));
    expect(await screen.findByTestId('raster-overlay')).toHaveAttribute(
      'data-bounds',
      JSON.stringify([[12.94, 18], [13, 18.08]]),
    );

    fireEvent.change(await screen.findByLabelText('GIS analysis result'), {
      target: { value: 'result-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add result layer' }));
    await waitFor(() => expect(screen.getAllByTestId('raster-overlay')).toHaveLength(2));

    const opacity = screen.getByLabelText('Opacity NDVI · 2024-02-03');
    fireEvent.change(opacity, { target: { value: '0.35' } });
    expect(screen.getAllByTestId('raster-overlay')
      .some((overlay) => overlay.getAttribute('data-opacity') === '0.35')).toBe(true);

    const manager = screen.getByRole('complementary', { name: 'Layer manager' });
    const rows = () => [...manager.querySelectorAll('.gis-layer-list > li')];
    expect(rows()[0]).toHaveTextContent('NDVI');
    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Send backward' }));
    expect(rows()[0]).toHaveTextContent('scene.tif');

    fireEvent.click(within(rows().find((row) => row.textContent.includes('NDVI')))
      .getByRole('button', { name: 'Bring forward' }));
    expect(rows()[0]).toHaveTextContent('NDVI');

    fireEvent.click(screen.getByLabelText('Show NDVI · 2024-02-03'));
    await waitFor(() => expect(screen.getAllByTestId('raster-overlay')).toHaveLength(1));
    fireEvent.click(screen.getByLabelText('Show NDVI · 2024-02-03'));
    await waitFor(() => expect(screen.getAllByTestId('raster-overlay')).toHaveLength(2));

    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Remove' }));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.getAllByTestId('raster-overlay')).toHaveLength(1));
    expect(downloadArtifact).toHaveBeenCalledWith('scene-1', 'preview.png');
    expect(downloadArtifact).toHaveBeenCalledWith('result-1', 'preview.png');
  });

  it('refuses scenes without EPSG:4326 preview bounds and retries failed preview retrieval', async () => {
    const sceneWithoutBounds = {
      ...scene,
      metadata: { ...scene.metadata, preview: { description: 'Old preview without map grid.' } },
    };
    downloadArtifact.mockRejectedValueOnce(new Error('network unavailable'));
    getImageryScene.mockResolvedValueOnce(sceneWithoutBounds);
    const legacyView = render(<GISWorkspace scenes={[sceneWithoutBounds]} />);
    fireEvent.change(screen.getByLabelText('GIS saved scene'), {
      target: { value: 'scene-1' },
    });
    await waitFor(() => expect(legacyView.getByRole('button', { name: 'Add scene preview' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Add scene preview' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Re-ingest it to create an aligned preview.');
    expect(screen.queryByTestId('leaflet-map')).not.toBeInTheDocument();
    legacyView.unmount();

    const aligned = render(<GISWorkspace scenes={[scene]} />);
    fireEvent.change(aligned.getByLabelText('GIS saved scene'), {
      target: { value: 'scene-1' },
    });
    await waitFor(() => expect(aligned.getByRole('button', { name: 'Add scene preview' })).toBeEnabled());
    fireEvent.click(aligned.getByRole('button', { name: 'Add scene preview' }));
    expect(await aligned.findByRole('alert')).toHaveTextContent('Preview unavailable.');
    fireEvent.click(aligned.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(aligned.getByTestId('raster-overlay')).toBeInTheDocument());
    expect(downloadArtifact).toHaveBeenCalledTimes(2);
  });

  it('keeps raster controls available when OpenStreetMap tiles fail', async () => {
    render(<GISWorkspace scenes={[scene]} />);
    fireEvent.change(screen.getByLabelText('GIS saved scene'), {
      target: { value: 'scene-1' },
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add scene preview' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Add scene preview' }));
    await screen.findByTestId('raster-overlay');
    fireEvent.click(screen.getByTestId('tile-layer'));
    expect(screen.getByText(/OpenStreetMap tiles are unavailable/)).toBeInTheDocument();
    expect(screen.getByTestId('raster-overlay')).toBeInTheDocument();
  });

  it('shows an empty map and retries a failed saved-results request', async () => {
    const onRefreshResults = vi.fn();
    render(
      <GISWorkspace
        resultsError="Saved analysis results could not be loaded."
        onRefreshResults={onRefreshResults}
      />,
    );
    expect(screen.getByText('No map layers selected')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Saved analysis results could not be loaded.');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRefreshResults).toHaveBeenCalledOnce();
    expect(screen.getByLabelText('GIS analysis result')).toBeEnabled();
  });
});
