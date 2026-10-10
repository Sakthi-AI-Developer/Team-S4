import { describe, expect, it } from 'vitest';
import { createReportData, interpretationFor, reportToCsv, reportToJson } from './analysisReport';

describe('analysis report exports', () => {
  const entry = {
    id: 'a'.repeat(32),
    analysis: 'NDVI',
    status: 'completed',
    created_at: '2026-01-01T12:00:00Z',
    history: {
      source_scenes: [{
        role: 'Scene',
        id: 'scene-1',
        filename: 'image,"north".tif\nsecond line',
        acquisition_date: null,
        download_url: 'https://private.example.test',
      }],
      method: 'NDVI',
      parameters: { threshold: 0.2, token: 'never-export' },
      statistics: {
        Summary: { mean: 0.42, formula: '=HYPERLINK("bad")', private_url: 'never-export' },
      },
      metrics: { negative_value: -0.25, formula_text: '  +SUM(1,2)' },
      spatial_metadata: { crs: 'EPSG:32610' },
    },
  };

  it('quotes CSV commas, quotes and line breaks and neutralizes formulas', () => {
    const csv = reportToCsv(createReportData(entry));
    expect(csv).toContain('source_scenes.0.filename,"image,""north"".tif\nsecond line"');
    expect(csv).toContain("'=HYPERLINK");
    expect(csv).toMatch(/'\s+\+SUM\(1,2\)/);
    expect(csv).toContain('metrics.negative_value,-0.25');
  });

  it('serializes safe JSON metadata without owner or storage fields', () => {
    const report = createReportData(entry, {
      owner_id: 'another-user',
      error: 'internal failure',
      summary: { object_key: 'private/path', storage_url: 'https://private.example.test' },
    });
    const json = reportToJson(report);
    expect(JSON.parse(json)).toMatchObject({
      id: entry.id,
      analysis: 'NDVI',
      statistics: { Summary: { mean: 0.42, formula: '=HYPERLINK("bad")' } },
    });
    expect(json).not.toContain('another-user');
    expect(json).not.toContain('private.example.test');
    expect(json).not.toContain('never-export');
    expect(json).not.toContain('object_key');
  });

  it('uses cautious interpretations for spectral clusters and candidate changes', () => {
    expect(interpretationFor(createReportData({ analysis: 'scene_kmeans' })))
      .toContain('not independently validated land-cover classes');
    expect(interpretationFor(createReportData({ analysis: 'scene_change_detection' })))
      .toContain('does not confirm a real-world environmental event');
  });
});
