import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, ImageOverlay, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { downloadArtifact, getResultImage } from '../services/api';

function Viewport({ bounds }) {
  const map = useMap();
  useEffect(() => {
    if (bounds?.length === 4) {
      map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], { padding: [24, 24], maxZoom: 14 });
    }
  }, [bounds, map]);
  return null;
}

export default function MapView({ result, layerName, resultId, layerKey, artifactName }) {
  const [imageState, setImageState] = useState('loading');
  const [imageUrl, setImageUrl] = useState(null);
  const bounds = result?.bounds;
  const center = bounds ? [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2] : [0, 0];
  const imageBounds = bounds ? [[bounds[1], bounds[0]], [bounds[3], bounds[2]]] : null;
  const requestedLayer = artifactName || layerKey || result?.visualization_url?.split('/').at(-1);

  useEffect(() => {
    let mounted = true;
    let objectUrl;
    setImageState('loading');
    setImageUrl(null);
    if (!resultId || !requestedLayer || (!artifactName && !result?.visualization_url)) {
      setImageState('error');
      return () => { mounted = false; };
    }
    const imageRequest = artifactName
      ? downloadArtifact(resultId, artifactName)
      : getResultImage(resultId, requestedLayer);
    imageRequest
      .then(({ data }) => {
        if (!mounted) return;
        objectUrl = URL.createObjectURL(data);
        setImageUrl(objectUrl);
        setImageState('loaded');
      })
      .catch(() => {
        if (mounted) setImageState('error');
      });
    return () => {
      mounted = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactName, result?.visualization_url, requestedLayer, resultId]);

  if (!bounds) {
    return <div className="map-empty"><div className="map-crosshair">◎</div><b>Georeferenced results will appear here</b><span>No map location is assumed. Load valid rasters and run an analysis.</span></div>;
  }

  return (
    <div className="map-frame">
      <MapContainer key={resultId} center={center} zoom={8} scrollWheelZoom className="leaflet-map">
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {imageUrl && <ImageOverlay
          key={`${resultId}-${layerName}`}
          url={imageUrl}
          bounds={imageBounds}
          opacity={0.9}
          eventHandlers={{
            load: () => setImageState('loaded'),
            error: () => setImageState('error'),
          }}
        />}
        <Viewport bounds={bounds} />
      </MapContainer>
      <div className="map-layer-tag">{layerName} · GeoTIFF extent</div>
      {imageState === 'loading' && <div className="map-overlay-status">Loading georeferenced layer…</div>}
      {imageState === 'error' && <div className="map-overlay-status error">Unable to load this map layer.</div>}
    </div>
  );
}
