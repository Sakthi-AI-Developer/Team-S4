# Satellite Intelligence & Land Monitoring

Stage A is an offline/pre-downloaded Sentinel-2 GeoTIFF MVP. It derives spectral indices, a transparent land-cover baseline, historical NDVI change, maps, and downloadable outputs from local data. The system does not download satellite imagery or invent sample analysis results.

## Problem statement

The project addresses the hackathon challenge, “AI/ML based system for deriving value added parameters using satellite imagery,” by turning multispectral imagery into interpretable indicators and visual summaries. The rule-based land-cover baseline is not a trained AI/ML model or ground truth.

## Features

- Local Sentinel-2 band discovery and validation (B02, B03, B04, B08, B11)
- NDVI, McFeeters NDWI, NDBI, heuristic land-cover classes, and NDVI change detection
- Nodata and invalid numeric handling; required-band CRS, transform, and dimension checks
- GeoTIFF outputs preserving projection and transform, plus web overlays and JSON reports
- Phase 4 metadata, data-quality, visualization, and ML-ready model-status infrastructure for richer geospatial inspection
- Phase 5 provider abstraction for local and live satellite data sources, AOI validation, and cache-aware product handling
- React dashboard with georeferenced map layers, statistics, charts, careful deterministic insights, and safe result downloads
- FastAPI health and dataset status endpoints; useful empty-dataset state when imagery is absent

## Architecture

`React dashboard → FastAPI → LocalProvider/LiveProvider → Common data loader → raster processing → GeoTIFF / PNG / JSON outputs`

`LocalDataProvider` remains the Stage-A data-source boundary, and the new provider abstraction keeps live Sentinel-2 integration separate while reusing the same preprocessing and analysis pipeline. Live satellite access requires explicit provider configuration; without credentials, the application remains in the local dataset mode and continues to work without fabricating data. See [docs/architecture.md](docs/architecture.md).

## Technologies

- Backend: Python 3.11+, FastAPI, Uvicorn, NumPy, Rasterio, Pillow
- Frontend: React, Vite, Tailwind CSS, Axios, React Leaflet, Leaflet, Recharts
- Tests: pytest, FastAPI TestClient, synthetic arrays and temporary GeoTIFFs

## Installation

Use Python 3.11 or newer and Node.js 20 or newer. From the project root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `DATA_DIR`, `OUTPUT_DIR`, `API_HOST`, `API_PORT`, and `CORS_ORIGINS` as needed. Relative paths resolve from the backend working directory. The defaults point at the included `backend/data` structure.

## Dataset structure

Place single-band GeoTIFFs directly in these folders (files are not bundled):

```text
backend/data/
├── current/
│   ├── B02.tif
│   ├── B03.tif
│   ├── B04.tif
│   ├── B08.tif
│   └── B11.tif
├── historical/
│   ├── B02.tif
│   ├── B03.tif
│   ├── B04.tif
│   ├── B08.tif
│   └── B11.tif
└── outputs/
```

Filenames may include other text but must contain a recognizable band token such as `B08`. One file per band is used; if duplicates are found, the first alphabetically is selected. Bands in each analysis must have identical dimensions, CRS, and geotransform. The current and historical rasters must also be aligned for change detection; the application reports mismatches rather than silently warping or resampling scientific measurements.

If current imagery is absent, the dashboard says: “No satellite dataset found. Please add Sentinel-2 GeoTIFF bands to backend/data/current/.” NDVI requires B04 and B08; NDWI requires B03 and B08; NDBI requires B08 and B11; land-cover baseline requires B03, B04, B08, and B11. Historical comparison requires aligned B04 and B08 in both periods.

## Start backend

```powershell
cd backend
uvicorn main:app --reload
```

The API is available at `http://127.0.0.1:8000`; interactive docs at `/docs`. Although the original request shorthand was `uvicorn main --reload`, Uvicorn's valid ASGI app notation requires `main:app`.

## Deploy the API to Render

The repository-root `render.yaml` defines a Python Web Service with its root directory set to `satellite-intelligence/backend`. Render installs that directory's `requirements.txt` and starts `main:app` on `0.0.0.0:$PORT`. It pins Python to 3.12.11, which is within the documented Python 3.11+ range and is supported by the backend's geospatial and machine-learning dependencies.

Set `CORS_ORIGINS` in the Render service environment to the deployed frontend's origin (comma-separated if there is more than one). `SATELLITE_PROVIDER` defaults to `local`; Copernicus credentials are only needed when explicitly enabling live satellite access. The `/api/health` endpoint is configured as the Render health check.

## Start frontend

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL (normally `http://127.0.0.1:5173`). Set `VITE_API_URL` to override the default API origin `http://127.0.0.1:8000`. Raster rendering is georeferenced from the output transform and CRS. OpenStreetMap tiles provide optional web basemap context; the local raster analysis itself does not depend on satellite APIs. Without internet access, the calculated overlay and local analysis remain available, but the optional basemap may not load.

## Run tests

```powershell
cd backend
pytest -q
```

Tests use synthetic NumPy arrays only for tests and temporary synthetic GeoTIFFs; no test fixture is represented as real satellite imagery.

## Scientific methods

- **NDVI:** `(B08 - B04) / (B08 + B04)`. The UI vegetation share uses a configurable code threshold of `0.3` and is not a universal crop-health measure.
- **NDWI:** McFeeters formulation `(B03 - B08) / (B03 + B08)`. Positive values above the configurable `0.0` threshold are water-related indicators, not verified water extent.
- **NDBI:** `(B11 - B08) / (B11 + B08)`. Positive values above the configurable `0.0` threshold are a built-up indicator, not building detection.
- **Land cover:** Explicit index rules output Agriculture, Vegetation, Water, Built-up, and Bare land. These are heuristic classes with no calibrated probability or accuracy claim. Heuristic thresholds are currently baseline constants in the classifier.
- **Change:** `current NDVI - historical NDVI`. Relative mean change is omitted when the historical mean is zero/undefined. Positive and negative pixel proportions refer to valid aligned pixels, not causal change.

Area is reported only when the input CRS is projected and its axis units can be converted to metres. Geographic-degree pixels are not assigned a misleading fixed area.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Service status |
| GET | `/api/dataset` | Current/historical band and georeferencing summary |
| GET | `/api/metadata` | Raster metadata for the current and historical datasets |
| GET | `/api/data-quality` | Data completeness, nodata, CRS, and quality warnings |
| GET | `/api/model/status` | ML-ready land-cover model status and training guardrails |
| GET | `/api/visualization/{kind}` | PNG preview for `rgb`, `ndvi`, `ndwi`, `ndbi`, `landcover`, or `change` |
| POST | `/api/analyze/ndvi` | Calculate NDVI |
| POST | `/api/analyze/ndwi` | Calculate NDWI |
| POST | `/api/analyze/ndbi` | Calculate NDBI |
| POST | `/api/analyze/landcover` | Run baseline class rules |
| POST | `/api/analyze/change_detection` | Compare aligned current/historical NDVI |
| POST | `/api/analyze/all` | Attempt each analysis and report unavailable results individually |
| GET | `/api/results` | List persisted result bundles |
| GET | `/api/results/{result_id}` | Read one JSON summary |
| GET | `/api/results/{result_id}/download/{file_key}` | Download an available GeoTIFF or JSON report |
| GET | `/api/results/{result_id}/image/{analysis_key}` | Fetch the rendered map overlay |

Result identifiers are server-generated. Download endpoints only expose known output filenames; arbitrary filesystem paths are not accepted. API errors return a readable `detail` message without exposing stack traces.

## Limitations

- Inputs are expected to be pre-downloaded, single-band, co-registered GeoTIFF files. The system rejects misalignment; it does not automatically resample.
- Cloud/shadow masking, atmospheric correction, and radiometric harmonization are not inferred. Inputs should be prepared consistently by the user.
- The rule-based class labels and index thresholds are screening indicators, not validated land-use ground truth.
- Change detection is sensitive to acquisition date, cloud conditions, preprocessing differences, and seasonal variation.
- Full rasters are loaded for processing, so available system memory limits the practical image size.
- OpenStreetMap background tiles require internet; data analysis uses local files.

## Stage B roadmap (not implemented)

Add a live-data provider implementing the provider boundary, then AOI/date/cloud filtering and authenticated imagery retrieval. Keep the existing processing functions unchanged and validate retrieved products for the same CRS, transform, nodata, and band contracts.
