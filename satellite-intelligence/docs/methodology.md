# Processing methodology

## Pipeline

```text
Pre-downloaded Sentinel-2 GeoTIFF bands
        ↓
Local data discovery and band identification
        ↓
Raster reading, nodata masking, and alignment validation
        ↓
NDVI / NDWI / NDBI spectral indices
        ↓
Transparent index-threshold land-cover baseline
        ↓
Aligned historical NDVI difference (when historical data exists)
        ↓
Statistics and projected-CRS area estimates where valid
        ↓
GeoTIFF, georeferenced PNG overlay, and JSON summary
        ↓
Dashboard visualization and cautious decision support
```

## Input and preprocessing

The Stage A `LocalDataProvider` reads only `backend/data/current/` and `backend/data/historical/`. It discovers GeoTIFFs using Sentinel-2 band tokens B02, B03, B04, B08, and B11. Each band is read with Rasterio; its CRS, transform, dimensions, resolution, and nodata mask are retained. Invalid and non-finite values are excluded from analysis. No atmospheric correction, normalization, cloud-probability mask, reprojection, or resampling is performed implicitly.

Every combination of bands must have matching dimensions, CRS, and transform. Current and historical rasters must also be aligned to compare pixels. An incompatible input produces a useful validation error so the user can prepare aligned imagery explicitly.

Raster metadata is preserved as provenance, including acquisition date, platform, sensor, provider/source, CRS, resolution and NoData quality where present. Conflicting tags are omitted with warnings. Missing values remain unknown; tags supplied by an uploaded file are not independently verified. Sample and mock rasters are explicitly tagged synthetic and do not receive a fabricated observation date or geographic footprint.

## Derived indices

- **NDVI:** `(NIR - Red) / (NIR + Red)` using B08 and B04.
- **NDWI (McFeeters):** `(Green - NIR) / (Green + NIR)` using B03 and B08. Other NDWI formulations exist; this project reports which one it uses.
- **NDBI:** `(SWIR - NIR) / (SWIR + NIR)` using B11 and B08.

Division by zero is marked invalid rather than replaced by an invented value. Statistics use only valid pixels. Vegetation, water-related, and built-up percentages use documented, configurable index thresholds, not ground-truth labels.

## Land-cover baseline

`LandCoverClassifier` is an intentionally replaceable interface implemented as transparent rules:

1. Water if McFeeters NDWI > 0.1.
2. Otherwise built-up indicator if NDBI > 0.05.
3. Otherwise vegetation if NDVI ≥ 0.45.
4. Otherwise agriculture if NDVI ≥ 0.25.
5. Otherwise bare land.

The priority order is significant. Class percentages are shares of valid pixels. Area is calculated only for projected CRSs with known conversion to square metres. “Baseline/heuristic confidence” is explicitly not a calibrated model probability; this baseline does not claim accuracy.

The repository also contains an untrained Random Forest wrapper, but no training data, labels, fitted model artifact, or held-out test set is present. It is not the classifier used for dashboard land-cover outputs. Supervised classification accuracy cannot be established from this repository's current data.

## Historical change

NDVI is calculated independently for current and historical bands. Per-pixel change is `current_NDVI - historical_NDVI`. A pixel is positive or negative when the valid difference is respectively above or below zero. Relative mean percentage change uses the absolute historical mean as denominator and is undefined when that mean is zero or unavailable. This is a descriptive image comparison and does not establish cause.

This is a single paired-period comparison, not a repeated trend. Risk indicators use code-defined screening thresholds and report their evidence and limitations; they are not calibrated risk scores or causal diagnoses. Temporal summaries and linear forecasts require distinct acquisition-date tags in the raster metadata. Since local dates are unverified user-supplied tags and the folder layout holds only one current and one historical dataset, forecast/evaluation output must be interpreted as insufficient or exploratory rather than independently validated performance.

## Outputs and map

Each analysis receives a server-generated result identifier. Output folders contain available GeoTIFFs, colorized transparent PNG overlays, and a JSON summary. The GeoTIFF copies the source CRS and affine transform and uses an explicit nodata value. Overlay bounds are derived from those geospatial metadata and transformed to WGS84 for Leaflet; the application does not invent coordinates.

## Limitations and responsible interpretation

Results depend on input calibration, acquisition conditions, cloud/shadow contamination, and temporal consistency. Land-cover rules and index thresholds are a hackathon baseline; they require local validation before operational decisions. Output percentages and summaries should be interpreted as satellite-derived indicators, not absolute real-world conclusions. Ground observations should be used where required.

The spatial-analysis endpoint summarizes the full raster and rejects AOI input because clipping has not been implemented. A deterministic 4x4 synthetic example is available at `/api/geoai/demo`; it runs in memory, is explicitly labelled synthetic, has no coordinates or date, and does not create or modify user analysis records. The mock satellite provider also emits local-CRS synthetic rasters, never real scene metadata. Live satellite search/download, independent satellite metadata verification, validated supervised model metrics, and ground-truth evaluation remain unimplemented.
