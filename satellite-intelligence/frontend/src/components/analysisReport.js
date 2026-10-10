const PRIVATE_FIELD = /owner|error|url|path|bucket|storage|token|secret|credential|download/i;

function cleanValue(value) {
  if (Array.isArray(value)) return value.map(cleanValue);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !PRIVATE_FIELD.test(key))
      .map(([key, child]) => [key, cleanValue(child)]),
  );
}

export function createReportData(entry, detail = {}) {
  const history = cleanValue(entry?.history ?? {});
  return {
    id: entry?.id ?? detail?.id ?? null,
    analysis: entry?.analysis ?? detail?.analysis ?? 'Analysis',
    status: entry?.status ?? detail?.status ?? 'unknown',
    created_at: entry?.created_at ?? detail?.created_at ?? null,
    completed_at: entry?.completed_at ?? detail?.completed_at ?? null,
    source_scenes: history.source_scenes ?? [],
    method: history.method ?? null,
    parameters: history.parameters ?? {},
    threshold: history.threshold ?? null,
    threshold_units: history.threshold_units ?? null,
    statistics: history.statistics ?? {},
    metrics: history.metrics ?? {},
    limitations: history.limitations ?? null,
    warnings: history.warnings ?? [],
    spatial_metadata: history.spatial_metadata ?? {},
  };
}

function formatScalar(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function csvCell(value) {
  let text = formatScalar(value);
  const startsWithFormula = /^[\t\r\n ]*[=+\-@]/.test(text);
  if (startsWithFormula && !Number.isFinite(Number(text.trim()))) text = `'${text}`;
  if (/[",\r\n]/.test(text)) text = `"${text.replaceAll('"', '""')}"`;
  return text;
}

function appendCsvRows(rows, prefix, value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      appendCsvRows(rows, `${prefix}.${key}`, child);
    }
    return;
  }
  rows.push([prefix, Array.isArray(value) ? JSON.stringify(value) : value]);
}

export function reportToCsv(report) {
  const rows = [['field', 'value']];
  for (const key of ['id', 'analysis', 'status', 'created_at', 'completed_at', 'method', 'threshold', 'threshold_units', 'limitations']) {
    if (report[key] !== null && report[key] !== undefined) rows.push([key, report[key]]);
  }
  for (const [index, warning] of report.warnings.entries()) {
    rows.push([`warnings.${index}`, warning]);
  }
  for (const [index, scene] of report.source_scenes.entries()) {
    for (const key of ['role', 'id', 'filename', 'acquisition_date']) {
      if (scene[key] !== null && scene[key] !== undefined) {
        rows.push([`source_scenes.${index}.${key}`, scene[key]]);
      }
    }
  }
  for (const [group, values] of [
    ['parameters', report.parameters],
    ['metrics', report.metrics],
    ['statistics', report.statistics],
    ['spatial_metadata', report.spatial_metadata],
  ]) {
    for (const [key, value] of Object.entries(values)) {
      appendCsvRows(rows, `${group}.${key}`, value);
    }
  }
  return `${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`;
}

export function reportToJson(report) {
  return `${JSON.stringify(cleanValue(report), null, 2)}\n`;
}

export function interpretationFor(report) {
  const analysis = `${report.analysis} ${report.method ?? ''}`.toLowerCase();
  if (analysis.includes('cluster') || analysis.includes('k-means') || analysis.includes('kmeans')) {
    return 'Cluster identifiers represent spectral groups from the selected input bands. They are not independently validated land-cover classes.';
  }
  if (analysis.includes('change')) {
    return 'Change output marks candidate spectral/index differences between the selected scenes. It does not confirm a real-world environmental event or its cause.';
  }
  if (analysis.includes('ndwi')) {
    return 'NDWI is a green/NIR spectral index. Its numeric values are not, by themselves, a confirmed water classification.';
  }
  if (analysis.includes('ndvi')) {
    return 'NDVI is a red/NIR vegetation index. Its numeric values are not, by themselves, a confirmed ecological condition or land-cover class.';
  }
  return 'Reported values describe the stored analysis output and do not independently establish scientific interpretation or ground truth.';
}
