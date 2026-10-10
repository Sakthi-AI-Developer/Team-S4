# Satellite Vision hackathon demonstration script

**Suggested length:** 5–7 minutes  
**Run mode:** local source checkout, local FastAPI + Vite, no cloud credentials required. The deterministic in-memory example is synthetic, not a real satellite observation.

## Before presenting

1. Start the backend from `satellite-intelligence/backend` with `uvicorn main:app --reload`.
2. Start the frontend from `satellite-intelligence/frontend` with `npm run dev`.
3. Open the Vite URL printed by the dev server.
4. The repository does not bundle an authoritative satellite GeoTIFF dataset. For the dependable short demo, click **Demo mode**. This executes `GET /api/geoai/demo`; it does not require a dataset folder or satellite provider.
5. If showing real user-supplied GeoTIFFs instead, prepare aligned single-band files with the required B03/B04/B08/B11 tokens in `backend/data/current/` (and optionally aligned, dated historical bands). Do not use the shared unauthenticated public-demo API for private imagery.

If authentication is configured, sign in with an authorized demo account before using protected endpoints. Local demo mode without Auth is intentionally shared and is suitable only for synthetic/non-sensitive inputs.

## Spoken script and actions

### 1. Introduce the problem (about 40 seconds)

“Satellite imagery contains spectral information that can help people inspect vegetation, water-related signals, and built-up indicators across land. Raw multispectral rasters are difficult to compare directly. Satellite Vision is a small, transparent processing and visualization system: it calculates documented indices from supplied, aligned GeoTIFF bands and exposes the results through a web dashboard. It is a prototype for exploration, not an operational land-cover authority.”

### 2. Set an honest scope (about 25 seconds)

“This build processes pre-downloaded input files. Live satellite scene discovery and downloading are not currently operational. The demonstration I am about to run is a deterministic synthetic example, deliberately separated from uploaded datasets and saved analysis records.”

### 3. Open the dashboard and synthetic demo (about 50 seconds)

**Action:** Show the dashboard and click **Demo mode**.

“The demo uses a fixed 4-by-4 in-memory set of example band values. There is no acquisition date, sensor identity, real-world coordinate system, or saved user artifact. The page labels the example synthetic so it cannot be mistaken for a live scene.”

**Observed output from the current code's local demo endpoint:** 16 valid pixels; mean NDVI approximately `0.278`, mean McFeeters NDWI approximately `-0.297`, and mean NDBI approximately `-0.017`. The threshold baseline assigned 8 pixels to Built-up, 4 to Vegetation, and 4 to Bare land; Agriculture and Water each received 0 pixels. These are outputs of the fixed synthetic fixture only. They are not measurements, accuracy metrics, or real land-cover counts.

### 4. Explain the indices and class rules (about 60 seconds)

“For Sentinel-2-style band assignments, NDVI is `(B08 - B04) / (B08 + B04)`, McFeeters NDWI is `(B03 - B08) / (B03 + B08)`, and NDBI is `(B11 - B08) / (B11 + B08)`. We exclude invalid pixels and zero denominators. The land-cover output is a rule baseline: water-related NDWI greater than 0.1, then built-up NDBI greater than 0.05, then vegetation NDVI at least 0.45, agriculture NDVI at least 0.25, otherwise bare land. The ordering matters. These labels are heuristic indicators, not a trained model or validated map.”

### 5. Describe the real-data workflow (about 60 seconds)

**Optional action if prepared aligned GeoTIFFs exist:** Select or upload the prepared band files, then run a supported analysis. If no real test rasters are present, do not pretend this step was run; describe it using the empty-data message.

“For a real run, the application finds supplied single-band GeoTIFFs by band token, reads arrays and raster metadata, checks required bands and alignment, applies nodata/finite-pixel masks, calculates the selected product, and returns a summary with downloadable outputs when available. It does not silently reproject, resample, cloud-mask, or clip to an AOI. The same-size/CRS/transform check is an important guardrail, but source tags are supplied metadata, not independent provenance verification.”

### 6. Maps, change and insights (about 45 seconds)

“The dashboard can display returned index maps, charts and provenance for supported analyses. Historical change is `current NDVI - historical NDVI` on valid aligned pixels. It is a comparison, not a causal explanation. Date tags are not independently verified. If the source data are missing, undated or insufficient, the app should show that limitation rather than manufacture a trend.”

### 7. Persistence and security (about 45 seconds)

“The repository includes optional Supabase Auth verification, owner-scoped analysis/job/artifact metadata, PostgreSQL migrations, and private-bucket artifact code. Local tests cover these paths with mocks and SQLite-backed persistence. The actual production database, Auth and Storage workflow was not verified, so I will not claim cloud persistence is operational. Source uploads on Render's local filesystem can also be ephemeral.”

“The currently configured public page is an older build without a visible sign-in control. Requests to its results-list endpoint returned HTTP 200 both without a token and with a malformed test bearer token; response bodies were discarded and not inspected. This means I cannot confirm that the deployed endpoint enforces authentication. I will not sign in, upload private imagery, or claim those results are protected. I am using the local synthetic demonstration instead.”

### 8. Close (about 25 seconds)

“The contribution is a reproducible, explainable pipeline and dashboard foundation, with explicit scientific and deployment limits. Before operational use, it needs verified source scenes, cloud masking and reprojection workflows, AOI clipping, independent ground truth, a trained/evaluated model where appropriate, and a successful authenticated cloud deployment.”

## Backup plan

- If the live Vercel/Render sites fail, use the local Vite and FastAPI services; do not repeatedly submit load to production.
- If the satellite provider is unavailable, use the in-memory synthetic demo. It needs no provider or external basemap.
- If the local dataset is empty, show the explicit empty-data state and use the synthetic demo rather than inventing a raster.
- If Auth/Supabase is unavailable, do not claim sign-in, saved private records, or cloud artifact retrieval. Use the local non-sensitive synthetic workflow.
- If the map basemap has no network, explain that raster processing and the synthetic demo do not depend on OpenStreetMap tiles.

## Likely questions

**Is the demo image from a real satellite?** No. It is a fixed in-memory synthetic calculation fixture without geography or an acquisition date.

**Is the land-cover model AI/ML trained?** No. The active output is an explicit index-threshold classifier. A Random Forest wrapper is untrained and is not the active classifier.

**What accuracy did it achieve?** No ground-truth labels or held-out satellite evaluation set are included, so no classification accuracy, precision, recall or F1 score can be claimed.

**Can it fetch Sentinel scenes or clip an AOI?** Not in the current implementation. Live search/download are unimplemented; AOI clipping is rejected rather than silently ignored.

**Does production Supabase work?** No live database CRUD, valid Auth session, private Storage object operation, or authenticated analysis was verified. Credentials and a test raster were unavailable. The configured frontend/backend are on older/different builds; the deployed results endpoint returned HTTP 200 to both missing and malformed credentials. Do not use it with private data until redeployed and checked.

**Is the forecast validated?** No. It requires dated observations and reports insufficient data where unavailable; current local folder history is not a validated time series.
