import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, ImageOverlay, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { getApiResourceUrl } from '../services/api';

function Viewport({ bounds }) {
  const map = useMap();
  useEffect(() => {
    if (bounds?.length === 4) {
      map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], { padding: [24, 24], maxZoom: 14 });
    }
  }, [bounds, map]);
  return null;
}

export default function MapView({ result, layerName, resultId }) {
  const [imageState, setImageState] = useState('loading');
  const bounds = result?.bounds;
  const imageUrl = resultId && result?.visualization_url
    ? getApiResourceUrl(result.visualization_url)
    : null;
  const center = bounds ? [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2] : [0, 0];
  const imageBounds = bounds ? [[bounds[1], bounds[0]], [bounds[3], bounds[2]]] : null;
  useEffect(() => setImageState('loading'), [imageUrl]);

  if (!bounds || !imageUrl) {
    return <div className="map-empty"><div className="map-crosshair">◎</div><b>Georeferenced results will appear here</b><span>No map location is assumed. Load valid rasters and run an analysis.</span></div>;
  }

  return (
    <div className="map-frame">
      <MapContainer key={resultId} center={center} zoom={8} scrollWheelZoom className="leaflet-map">
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ImageOverlay
          key={`${resultId}-${layerName}`}
          url={imageUrl}
          bounds={imageBounds}
          opacity={0.9}
          eventHandlers={{
            load: () => setImageState('loaded'),
            error: () => setImageState('error'),
          }}
        />
        <Viewport bounds={bounds} />
      </MapContainer>
      <div className="map-layer-tag">{layerName} · GeoTIFF extent</div>
      {imageState === 'loading' && <div className="map-overlay-status">Loading georeferenced layer…</div>}
      {imageState === 'error' && <div className="map-overlay-status error">Unable to load this map layer.</div>}
    </div>
  );
}
