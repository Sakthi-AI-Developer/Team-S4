import { useState } from 'react';
import { downloadResult } from '../services/api';

const FILES = [
  ['ndvi', 'NDVI GeoTIFF'],
  ['ndwi', 'NDWI GeoTIFF'],
  ['ndbi', 'NDBI GeoTIFF'],
  ['landcover', 'Land-use GeoTIFF'],
  ['change_detection', 'Change GeoTIFF'],
  ['report', 'Summary JSON'],
];

export default function DownloadPanel({ resultId, analyses, onError }) {
  const [downloading, setDownloading] = useState('');

  async function handleDownload(key, label, downloadId) {
    if (!downloadId || downloading) return;
    setDownloading(key);
    try {
      const response = await downloadResult(downloadId, key);
      const blobUrl = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = key === 'report' ? 'summary.json' : `${key}.tif`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      const detail = error.response?.data?.detail;
      onError(typeof detail === 'string' ? detail : `Unable to download ${label}.`);
    } finally {
      setDownloading('');
    }
  }

  return (
    <div className="download-panel-content">
      <div className="download-list">
        {FILES.map(([key, label]) => {
          const analysisResultId = analyses[key]?.result_id || resultId;
          const available = Boolean(analysisResultId && (key === 'report' || analyses[key]));
          return (
            <button
              className={`download-row ${available ? '' : 'disabled'}`}
              type="button"
              key={key}
              disabled={!available || Boolean(downloading)}
              onClick={() => handleDownload(key, label, analysisResultId)}
            >
              <span>{downloading === key ? '…' : '⇩'}</span>
              <b>{downloading === key ? 'Downloading' : label}</b>
              <small>{available ? 'Ready' : 'Unavailable'}</small>
            </button>
          );
        })}
      </div>
    </div>
  );
}
