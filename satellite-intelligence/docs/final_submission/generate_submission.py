from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor as WordColor
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches as SlideInches, Pt as SlidePt


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

NAVY = "102431"
NAVY_LIGHT = "183545"
TEAL = "27D3BD"
SKY = "85BFFF"
WHITE = "F5F8FA"
MUTED = "B7C6CE"
GOLD = "FFC86B"
RED = "FF857C"
GREEN = "80D7A7"
INK = "18313D"
PALE = "EAF4F5"
PALE_BLUE = "EEF4FB"
DOC_BLUE = "0F5261"


def get_demo_output() -> dict:
    from fastapi.testclient import TestClient

    from main import app

    response = TestClient(app).get("/api/geoai/demo")
    if response.status_code != 200:
        raise RuntimeError(
            f"The local synthetic demo endpoint returned HTTP {response.status_code}."
        )
    payload = response.json()
    if payload.get("data_classification") != "synthetic":
        raise RuntimeError("The demo endpoint did not identify its data as synthetic.")
    return payload


def set_cell_shading(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def set_cell_margins(cell, top=90, start=110, bottom=90, end=110) -> None:
    properties = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        element = OxmlElement(f"w:{side}")
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    properties.append(margins)


def style_doc_table(table, header=True) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            if header and row_index == 0:
                set_cell_shading(cell, DOC_BLUE)
            elif row_index % 2 == 0:
                set_cell_shading(cell, "F2F7F8")
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(1)
                for run in paragraph.runs:
                    run.font.name = "Aptos"
                    run.font.size = Pt(8.5)
                    if header and row_index == 0:
                        run.font.bold = True
                        run.font.color.rgb = WordColor(255, 255, 255)
                    else:
                        run.font.color.rgb = WordColor(24, 49, 61)


def add_doc_table(document, headers: list[str], rows: list[list[str]], widths=None) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for values in rows:
        cells = table.add_row().cells
        for index, value in enumerate(values):
            cells[index].text = str(value)
    if widths:
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Inches(width)
    style_doc_table(table)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def add_doc_bullets(document, items: list[str]) -> None:
    for item in items:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.add_run(item)


def add_doc_section(document, number: int, title: str, paragraphs=(), bullets=()) -> None:
    document.add_heading(f"{number}. {title}", level=1)
    for text in paragraphs:
        paragraph = document.add_paragraph(text)
        paragraph.paragraph_format.space_after = Pt(6)
    if bullets:
        add_doc_bullets(document, list(bullets))


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Satellite Vision | Phase 16  •  ")
    run.font.size = Pt(8)
    run.font.color.rgb = WordColor(90, 110, 120)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)


def build_report(demo: dict) -> Path:
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)

    normal = document.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10)
    normal.font.color.rgb = WordColor(34, 53, 62)
    normal.paragraph_format.space_after = Pt(5)
    for style_name, size, color in (
        ("Title", 30, DOC_BLUE),
        ("Heading 1", 16, DOC_BLUE),
        ("Heading 2", 12, DOC_BLUE),
    ):
        style = document.styles[style_name]
        style.font.name = "Aptos Display"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = WordColor.from_string(color)

    header = section.header.paragraphs[0]
    header.text = "SATELLITE VISION  |  TECHNICAL PROJECT REPORT"
    header.style = document.styles["Caption"]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_page_number(section.footer.paragraphs[0])

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(92)
    run = title.add_run("SATELLITE VISION")
    run.bold = True
    run.font.name = "Aptos Display"
    run.font.size = Pt(32)
    run.font.color.rgb = WordColor.from_string(DOC_BLUE)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Satellite Intelligence & Land Monitoring System")
    run.font.size = Pt(18)
    run.font.color.rgb = WordColor(59, 94, 105)
    tagline = document.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tagline.paragraph_format.space_before = Pt(12)
    run = tagline.add_run("Evidence-based technical report and final project audit")
    run.italic = True
    run.font.size = Pt(12)
    date_line = document.add_paragraph()
    date_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_line.paragraph_format.space_before = Pt(95)
    date_line.add_run(f"Prepared {date.today().isoformat()}").font.size = Pt(11)
    status = document.add_paragraph()
    status.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = status.add_run(
        "Local synthetic demonstration: verified  |  Live production: not ready"
    )
    run.bold = True
    run.font.color.rgb = WordColor(153, 91, 17)
    document.add_page_break()

    document.add_heading("Contents", level=1)
    for number, title_text in enumerate(
        [
            "Abstract",
            "Introduction",
            "Problem statement",
            "Project objectives",
            "Existing-system limitations",
            "Proposed system",
            "System architecture",
            "Hardware and software requirements",
            "Technology stack",
            "Module descriptions",
            "Data flow and processing workflow",
            "Satellite data sources and preprocessing",
            "Geospatial indices and formulas",
            "AI/ML methods and validation",
            "Database and storage design",
            "API design and authentication",
            "User interface and dashboard",
            "Testing methodology and actual results",
            "Performance evaluation",
            "Results and discussion",
            "Limitations",
            "Future enhancements",
            "Conclusion",
            "References",
        ],
        start=1,
    ):
        document.add_paragraph(f"{number}. {title_text}")
    document.add_paragraph(
        "Evidence status: claims below describe the checked-in source and explicitly identified tests. "
        "Passing software tests does not establish scientific accuracy or prove that production services work."
    )

    add_doc_section(
        document,
        1,
        "Abstract",
        [
            "Satellite Vision is a local-first geospatial analysis prototype that reads supplied, "
            "pre-downloaded single-band GeoTIFF rasters and calculates selected spectral indices, "
            "a transparent rule-based land-cover baseline, and aligned historical NDVI differences. "
            "A React dashboard presents analysis status, visual overlays, charts, provenance warnings, "
            "and result downloads. Optional code paths provide Supabase Auth validation, PostgreSQL "
            "metadata and private object storage.",
            "The current source and local tests verify software behavior on synthetic arrays, temporary "
            "rasters, mocked cloud transports and local persistence. No authoritative satellite scenes "
            "or labeled ground truth are bundled. At the latest local validation, the backend suite "
            "passed 92 tests, the frontend passed 28 tests, and the Vite production build succeeded. "
            "The synthetic demo endpoint returned HTTP 200 locally. The configured live deployments "
            "were stale/different from the current worktree; production Auth, PostgreSQL, Storage and "
            "a complete analysis journey remain unverified.",
        ],
    )
    add_doc_section(
        document,
        2,
        "Introduction",
        [
            "Multispectral Earth-observation products encode surface reflectance at different wavelengths. "
            "Simple band combinations can provide interpretable signals for vegetation, water-related "
            "features and built-up surfaces. Satellite Vision packages these calculations behind a "
            "local processing pipeline and browser dashboard.",
            "This is a hackathon-stage foundation rather than a validated operational monitoring service. "
            "The source requires users to provide compatible input scenes and explicitly surfaces missing "
            "data, unknown provenance, and insufficient temporal history.",
        ],
    )
    add_doc_section(
        document,
        3,
        "Problem statement",
        [
            "Users may have multispectral GeoTIFF bands but lack a convenient way to validate, compare, "
            "summarize, visualize and download derived products. A safe tool should expose its input "
            "assumptions and avoid turning an index threshold into an unsupported causal or land-use claim."
        ],
    )
    add_doc_section(
        document,
        4,
        "Project objectives",
        bullets=[
            "Discover supported band-token files and validate required bands and raster alignment.",
            "Compute NDVI, McFeeters NDWI, NDBI, a heuristic class map and optional aligned NDVI change.",
            "Preserve available geospatial metadata and identify unknown or synthetic provenance.",
            "Present results and limitations in a React dashboard with maps, charts and downloadable files.",
            "Provide a reproducible synthetic demo and regression tests without changing real user data.",
            "Offer optional owner-scoped Auth, PostgreSQL metadata and private Storage persistence.",
        ],
    )
    add_doc_section(
        document,
        5,
        "Existing-system limitations",
        paragraphs=[
            "The prototype improves on a manual band-inspection workflow by automating calculations and "
            "visualization, but it does not replace a validated Earth-observation processing chain. "
            "It does not download live scenes, verify scene metadata, perform atmospheric correction, "
            "apply cloud masks, reproject/resample mismatched inputs, or clip calculations to an AOI. "
            "The actual satellite sensor, calibration and acquisition context depend on user-supplied files "
            "and tags, which are not independently authenticated."
        ],
    )
    add_doc_section(
        document,
        6,
        "Proposed system",
        paragraphs=[
            "The implemented solution is an offline-first workflow: the user places or uploads suitable "
            "single-band GeoTIFFs, the FastAPI backend validates and calculates the requested product, "
            "and the dashboard renders available summaries and overlays. A separate in-memory synthetic "
            "demo demonstrates calculations without being confused with observations.",
            "Optional source-code integrations provide user authentication and durable artifact metadata/"
            "object storage. These integrations are not considered production-verified because credentials "
            "and successful matching deployments were unavailable during this audit."
        ],
    )
    add_doc_section(
        document,
        7,
        "System architecture",
        paragraphs=[
            "The browser application is built with React and Vite. It sends API requests to FastAPI, which "
            "reads local/preloaded raster bands through the provider/data-loading layer and calls separate "
            "processing modules. Result summaries and artifacts are returned through the API. When fully "
            "configured, the backend verifies bearer sessions with Supabase Auth, stores metadata in "
            "PostgreSQL, and stores binaries in a private Supabase Storage bucket.",
            "Architecture, request, processing, persistence, authentication and job lifecycle diagrams are "
            "provided in Satellite_Vision_Architecture.md. This implementation has no durable asynchronous "
            "worker: job creation and analysis occur in the same synchronous request process."
        ],
    )
    document.add_heading("Evidence-based feature status", level=2)
    add_doc_table(
        document,
        ["Status", "Features / evidence"],
        [
            [
                "Implemented and verified locally",
                "Index formulas and invalid-pixel handling; heuristic classifier; aligned NDVI change; "
                "synthetic demo; UI/API regression tests; local and mocked persistence behavior; "
                "production frontend build.",
            ],
            [
                "Implemented but not fully verified",
                "Supabase bearer verification; owner-scoped data/artifacts; PostgreSQL migrations and "
                "private Storage integration; deployed Vercel/Render application. Source/mocks exist, "
                "but live cloud user journey was not validated.",
            ],
            [
                "Planned or incomplete",
                "Live scene search/download, AOI clipping, automatic reprojection, cloud masking, "
                "trained/held-out-evaluated classifier, durable async worker, production-tested cloud storage.",
            ],
        ],
    )
    add_doc_section(
        document,
        8,
        "Hardware and software requirements",
        paragraphs=[
            "The project has no formally measured minimum hardware specification. The documented "
            "development requirements are Python 3.11+, Node.js 20+, and a browser. Backend libraries "
            "include FastAPI, Uvicorn, NumPy, Rasterio, PyProj, Shapely, Pillow and SQLAlchemy. Frontend "
            "libraries include React, Vite, Axios, Leaflet/React Leaflet, Recharts and Supabase JS.",
            "A local performance run used Windows 11, Python 3.14.3 and deterministic 512 x 512 synthetic "
            "rasters. Its sampled peak process RSS was about 245 MB for the 1-client run. CPU model and "
            "system RAM were not recorded, and this single-process result is not a minimum requirement or "
            "capacity promise. The Render Blueprint pins Python 3.12.11."
        ],
    )
    add_doc_section(
        document,
        9,
        "Technology stack",
        bullets=[
            "Frontend: React 19, Vite 6, Leaflet / React Leaflet, Recharts, Axios, Supabase JS.",
            "Backend: Python, FastAPI, Uvicorn, NumPy, Rasterio, PyProj, Shapely, Pillow.",
            "Persistence: SQLAlchemy and Alembic; PostgreSQL when configured; Supabase Storage for private binaries.",
            "Deployment configuration: Vercel frontend, Render FastAPI service; checked-in API health path `/api/ready`.",
            "Testing: pytest, FastAPI TestClient, temporary GeoTIFF fixtures, mocked HTTP transport, Vitest and Testing Library.",
        ],
    )
    add_doc_section(
        document,
        10,
        "Module descriptions",
        bullets=[
            "`backend/processing/`: band loading, alignment validation, index calculations, class baseline, change detection, provenance and visualization.",
            "`backend/geoai/`: spatial summaries, trend/forecast utilities, risk indicators, transitions, uncertainty and evaluation guardrails.",
            "`backend/satellite/`: provider interface, local/mock providers and cache; live scene retrieval is incomplete.",
            "`backend/api/routes.py`: dataset upload/status, analysis, demo, GeoAI, results, job and artifact endpoints.",
            "`backend/persistence/`: SQLAlchemy entities/repository, migrations integration, local/Supabase artifact manager and consistency handling.",
            "`frontend/src/`: dashboard state, API client, auth panel, lazy-loaded map/charts and downloadable output workflow.",
        ],
    )
    add_doc_section(
        document,
        11,
        "Data flow and processing workflow",
        paragraphs=[
            "The flow is: locate supported band-token GeoTIFFs; read raster values and metadata; check required bands, "
            "dimensions, CRS and transforms; create a combined valid-data mask; compute indices and optional "
            "classification/change products; produce statistics and outputs; return a summary and available "
            "artifact manifest to the UI. Invalid or misaligned datasets produce an explicit error rather than "
            "automatic reprojection or silent interpolation.",
            "The full raster is currently loaded for core calculations. Source-size, pixel-count, input-array and "
            "time budgets plus per-process admission control guard expected resource use, but do not constitute "
            "a strict total-RSS bound."
        ],
    )
    add_doc_section(
        document,
        12,
        "Satellite data sources and preprocessing",
        paragraphs=[
            "The operational analysis input is user-supplied or pre-downloaded single-band GeoTIFF data, usually "
            "identified by Sentinel-2-style tokens B02, B03, B04, B08 and B11. No sample scene is bundled and "
            "no live Sentinel scene was used to produce the project test results. The mock provider produces "
            "clearly labelled synthetic data.",
            "Rasterio reads data, CRS, affine transform, dimensions, resolution, nodata and available tags. The "
            "pipeline rejects unsupported/missing bands and mismatched grids. It does not perform atmospheric "
            "correction, normalization, cloud/shadow masking, automatic reprojection, resampling, or AOI clipping. "
            "Tags such as acquisition date/platform/sensor are provenance hints supplied with the input, not "
            "verified facts."
        ],
    )
    add_doc_section(
        document,
        13,
        "Geospatial indices and formulas",
        paragraphs=[
            "Band identities below follow the current implementation's Sentinel-2-style contract; the code "
            "does not prove that an arbitrary input is in fact from that sensor."
        ],
    )
    add_doc_table(
        document,
        ["Product", "Input bands", "Implemented equation / interpretation"],
        [
            ["NDVI", "B08 NIR; B04 red", "(B08 - B04) / (B08 + B04); vegetation greenness indicator."],
            ["McFeeters NDWI", "B03 green; B08 NIR", "(B03 - B08) / (B03 + B08); water-related indicator."],
            ["NDBI", "B11 SWIR; B08 NIR", "(B11 - B08) / (B11 + B08); built-up indicator."],
            ["NDVI change", "Aligned current and historical B04/B08", "NDVI_current - NDVI_historical; descriptive, not causal."],
        ],
    )
    document.add_paragraph(
        "Zero denominators and invalid/nodata pixels are excluded from valid statistics. The implementation does "
        "not clamp outputs to [-1, 1]; negative source values can result in warnings and values outside that range. "
        "Default thresholds include NDVI 0.3 for a vegetation share and NDWI/NDBI 0.0 for indicator summaries; "
        "these are application parameters, not universal ecological boundaries."
    )
    add_doc_section(
        document,
        14,
        "AI/ML methods and validation",
        paragraphs=[
            "The active land-cover product is not a trained AI/ML model. It is a deterministic baseline with precedence: "
            "Water (NDWI > 0.1), Built-up (NDBI > 0.05), Vegetation (NDVI >= 0.45), Agriculture (NDVI >= 0.25), "
            "then Bare land. The class labels and threshold agreement are not calibrated probabilities.",
            "A Random Forest wrapper is present but remains untrained and is not used for dashboard classification. "
            "No labeled training dataset, fitted model artifact, independent test labels, or satellite ground truth "
            "is included. Consequently supervised accuracy, precision, recall, F1 and confusion-matrix metrics "
            "cannot be established. Time-series evaluation code uses chronological hold-out and persistence baseline "
            "when enough observations exist, but current tagged local history is insufficient for independent "
            "forecast validation."
        ],
    )
    add_doc_section(
        document,
        15,
        "Database and storage design",
        paragraphs=[
            "SQLAlchemy metadata represents analyses, processing jobs, status history and analysis artifacts. "
            "Alembic revisions 0001 through 0004 add base persistence, ownership/artifact metadata, idempotency "
            "and a history index. Owner IDs and idempotency keys scope records; migrations are designed to preserve "
            "legacy rows rather than assign them to users. PostgreSQL stores metadata and JSON summaries, not large "
            "raster binaries.",
            "When properly configured, the backend uses the Supabase service-role credential for private object "
            "operations and stores the bucket/object key, media/type/size/timestamp and metadata in PostgreSQL. "
            "Artifact retrieval is routed through owner-checked backend endpoints; optional signed links expire. "
            "Local artifacts remain the offline fallback. Cloud database and Storage behavior was covered with "
            "mocks/local tests but was not verified against a live Supabase project."
        ],
    )
    add_doc_section(
        document,
        16,
        "API design and authentication",
        paragraphs=[
            "Representative endpoints include `/api/health`, `/api/ready`, `/api/auth/status`, `/api/dataset`, "
            "`/api/dataset/{period}/upload`, `/api/analyze/{analysis_key}`, `/api/analyze/all`, "
            "`/api/results`, `/api/jobs/{job_id}`, `/api/analyses/{analysis_id}`, `/api/results/{result_id}/artifacts`, "
            "`/api/geoai/demo`, `/api/geoai/history`, `/api/geoai/model-evaluation`, and "
            "`/api/visualization/{kind}`. Satellite search/download routes are present but live retrieval returns "
            "an unimplemented response.",
            "When auth is required, the backend forwards the bearer credential to Supabase Auth's user endpoint "
            "and scopes data access using the verified identity. Public paths include liveness/readiness/auth-status. "
            "CORS is restricted to configured origins. Frontend build settings are `VITE_API_URL`, "
            "`VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. Backend-only settings include `DATABASE_URL`, "
            "`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_STORAGE_BUCKET`. "
            "The service-role key and database URL must never be placed in Vite/browser settings. The deployed "
            "page checked on 2026-10-10 had no visible sign-in control. `GET /api/results?limit=1` returned "
            "HTTP 200 both without credentials and with a malformed test bearer token (the anonymous response "
            "was 29 bytes). Both bodies were discarded and not inspected; this establishes failure to reject those requests, "
            "not proof that user data was returned. Do not upload private data before redeployment and verification.",
            "The local source also refuses to send Supabase bearer or service-role credentials to a remote HTTP URL; "
            "HTTPS is required, with loopback HTTP allowed for development.",
            "Jobs transition through queued/running/completed/failed records in the request lifecycle. Processing is "
            "synchronous; no durable worker or queue is implemented. A per-process concurrency guard and idempotency "
            "support do not make execution distributed or background."
        ],
    )
    add_doc_section(
        document,
        17,
        "User interface and dashboard",
        paragraphs=[
            "The React UI includes dataset status and upload surfaces, per-analysis actions, a synthetic demo panel, "
            "analysis maps/overlays, charts, insight/quality messages, saved-result history, authentication UI when "
            "configured, and artifact download controls. Map and chart modules are loaded lazily. Empty dataset and "
            "insufficient-history states are represented explicitly.",
            "A local browser run against the current source displayed the synthetic demo label, three calculated index "
            "means and the rule-class distribution. This is a genuine run of the local synthetic endpoint, not a "
            "screenshot or result from the deployed site. The current Vercel deployment served an older bundle, so "
            "its UI is not evidence for the current source behavior."
        ],
    )
    add_doc_section(
        document,
        18,
        "Testing methodology and actual results",
        paragraphs=[
            "Backend tests use deterministic arrays, temporary GeoTIFFs, SQLite-backed repository checks and mocked "
            "HTTP/Auth/Storage transports. Frontend tests use Vitest, Testing Library and mocked API interactions. "
            "The local synthetic demo endpoint was independently invoked and returned HTTP 200 with the synthetic "
            "classification marker. These methods verify code paths but are not live cloud, provider, or scientific "
            "ground-truth tests."
        ],
    )
    add_doc_table(
        document,
        ["Check", "Latest recorded result", "Evidence scope"],
        [
            ["Backend pytest", "95 passed, 0 failed, 3 warnings", "Local test suite; mocks and temporary/local data."],
            ["Focused auth/CORS/analysis/demo regressions", "7 passed", "Local only; mocked Auth/Storage and synthetic/temporary raster data."],
            ["Frontend Vitest", "28 passed, 0 failed", "Local UI/API regression suite."],
            ["Vite production build", "Passed", "Local production bundle."],
            ["Synthetic demo API", "HTTP 200; synthetic scenario", "Current local source, fixed 4 x 4 in-memory fixture."],
            ["npm audit --audit-level=high", "0 vulnerabilities reported", "Full frontend dependency audit."],
            ["Python pip check", "Passed", "Installed dependencies have no reported conflicts."],
            ["Python dependency audit", "Not run", "pip-audit was not installed."],
            ["Live cloud CRUD/auth", "Not verified", "No valid local cloud credentials."],
            ["Live Vercel frontend", "PASS: HTTP 200, stale bundle", "Browser loaded index-BEv36dup.js; no sign-in control was visible."],
            ["Live Render health/readiness", "PASS: HTTP 200 after retry", "Initial 25-second requests timed out; longer retry succeeded."],
            ["Live results authorization", "FAIL: credentials not rejected", "Missing and malformed bearer requests returned HTTP 200; response bodies discarded."],
            ["Live CORS Idempotency-Key", "FAIL: preflight HTTP 400", "Live Access-Control-Allow-Headers omitted the header."],
            ["Live API route parity", "FAIL: current routes absent", "Auth, persistence-status and synthetic-demo routes returned HTTP 404."],
            ["Live DB/Auth/Storage/workflow", "BLOCKED", "No cloud credentials or valid GeoTIFF test input; no production writes attempted."],
            ["Live full analysis", "BLOCKED", "Configured deployment was stale/different; no production mutation."],
        ],
    )
    document.add_paragraph(
        "The backend suite emitted three warnings: a Starlette/httpx TestClient deprecation warning and two "
        "Alembic configuration deprecation warnings. No test failure was observed in that run."
    )
    add_doc_section(
        document,
        19,
        "Performance evaluation",
        paragraphs=[
            "A bounded local benchmark used deterministic synthetic 512 x 512 rasters and one request per simulated "
            "client. Before the Phase 14 changes, all requests succeeded at concurrency 1/5/10/25; after resource "
            "limits and per-process admission were introduced, one request was accepted and excess work was rejected "
            "with HTTP 503. The latest audit rerun is a separate single run; throughput of rejected attempts is not "
            "accepted-analysis capacity."
        ],
    )
    add_doc_table(
        document,
        ["Clients", "Earlier median/p95 ms", "Earlier sampled peak RSS bytes", "Later HTTP 200/503", "Accepted median/p95 ms", "Later sampled peak RSS bytes"],
        [
            ["1", "535.01 / 535.01", "251,760,640", "1 / 0", "445.61 / 445.61", "245,297,152"],
            ["5", "626.23 / 659.18", "373,923,840", "1 / 4", "384.91 / 384.91", "245,272,576"],
            ["10", "878.67 / 916.66", "492,388,352", "1 / 9", "358.39 / 358.39", "250,494,976"],
            ["25", "2,201.30 / 2,242.87", "892,571,648", "1 / 24", "440.76 / 440.76", "249,253,888"],
        ],
    )
    document.add_paragraph(
        "Final-audit rerun (2026-10-10; Windows 11, Python 3.14.3, FastAPI TestClient, no database or cloud) "
        "reported all-request median/p95, accepted-analysis median/p95, attempt/accepted throughput, and sampled "
        "peak RSS as follows:"
    )
    add_doc_table(
        document,
        ["Clients", "HTTP 200/503", "All median/p95 ms", "Accepted median/p95 ms", "Attempt/accepted per second", "Peak RSS bytes"],
        [
            ["1", "1 / 0", "631.87 / 631.87", "631.87 / 631.87", "1.42 / 1.42", "239,943,680"],
            ["5", "1 / 4", "49.98 / 1,774.40", "1,774.40 / 1,774.40", "2.56 / 0.51", "253,022,208"],
            ["10", "1 / 9", "74.86 / 928.00", "928.00 / 928.00", "9.74 / 0.97", "250,048,512"],
            ["25", "1 / 24", "154.39 / 232.03", "1,896.34 / 1,896.34", "11.83 / 0.47", "258,654,208"],
        ],
    )
    document.add_paragraph(
        "These are single local runs, not controlled benchmark statistics or deployment capacity claims. The latest "
        "rerun accepted one request at each level and intentionally rejected 80%/90%/96% at 5/10/25 clients. Its "
        "all-request latency is visibly variable and does not demonstrate an optimization. A separate warmed cProfile run attributed "
        "approximately 0.126 s to PNG encoding, 0.087 s to reading the selected input bands, and 0.10 s to local "
        "persistence. PostgreSQL, Supabase Storage, Vercel/Render latency, CPU model, and production load were not measured."
    )
    add_doc_section(
        document,
        20,
        "Results and discussion",
        paragraphs=[
            f"The current synthetic demo endpoint returned a 4 x 4 fixture with 16 valid pixels. Its means were "
            f"NDVI {demo['results']['ndvi']['statistics']['mean']:.3f}, "
            f"McFeeters NDWI {demo['results']['ndwi']['statistics']['mean']:.3f}, and "
            f"NDBI {demo['results']['ndbi']['statistics']['mean']:.3f}. Its five-class baseline assigned "
            f"{demo['results']['landcover']['class_distribution']['4']['pixel_count']} pixels to Built-up, "
            f"{demo['results']['landcover']['class_distribution']['2']['pixel_count']} to Vegetation, and "
            f"{demo['results']['landcover']['class_distribution']['5']['pixel_count']} to Bare land; no pixels "
            "were assigned to Water or Agriculture. These values demonstrate reproducible software calculations "
            "on synthetic reflectance-like values only; they do not imply real land cover or scientific accuracy.",
            "The source's strongest verified contribution is a transparent local workflow with input checks, explicit "
            "limitations, deterministic demo behavior, test coverage and bounded process-level resource controls. "
            "The deployment mismatch and missing live cloud checks are release blockers for relying on the production URLs."
        ],
    )
    add_doc_section(
        document,
        21,
        "Limitations",
        bullets=[
            "No verified live satellite search/download, AOI clipping, automatic warp/resampling, atmospheric correction, or cloud masking.",
            "No bundled authoritative imagery or labeled ground truth; no supported supervised accuracy scores.",
            "Rule-based classes and index thresholds are screening indicators and vary with sensor, calibration, season and preprocessing.",
            "Raster tags are not independently verified provenance. Current/historical local layout provides little temporal evidence.",
            "Core rasters are loaded into memory; limits are guards, not a hard total-memory ceiling.",
            "Analysis is synchronous; no durable queue, cross-instance admission, cancellation, or claim of automatic restart/resume.",
            "No live Supabase PostgreSQL, Auth or private Storage verification; cloud credentials were unavailable.",
            "Production Vercel/Render builds and route surface were stale/different from the current checked-in source at last probe.",
        ],
    )
    add_doc_section(
        document,
        22,
        "Future enhancements",
        bullets=[
            "Redeploy matching frontend/backend versions, align exact CORS settings, and verify the authenticated cloud workflow.",
            "Implement and validate a live satellite provider, retaining provider product identifiers, acquisition metadata and quality flags.",
            "Add tested AOI geometry validation/clipping, CRS-aware reprojection and resampling policies, and cloud/shadow masking.",
            "Assemble licensed, sensor-documented ground truth before training/evaluating a classifier; report class-wise metrics and uncertainty.",
            "Build independent, time-aware forecast evaluation with sufficient dated observations and a persistence baseline.",
            "Consider durable queue/worker architecture only after measuring demand and selecting supported hosting resources.",
            "Add reproducible staging integration checks and verify signed private artifact access without mutating production user data.",
        ],
    )
    add_doc_section(
        document,
        23,
        "Conclusion",
        [
            "Satellite Vision is a technically coherent local-first prototype that converts aligned raster bands into "
            "documented index products and transparent, explicitly unvalidated class indicators. Its deterministic "
            "synthetic demo and local test suites support a controlled hackathon presentation. It is not ready for "
            "production land-management decisions: satellite provenance, supervised accuracy, live cloud persistence, "
            "and the deployed user journey remain unverified. The local synthetic presentation is suitable for a demo; "
            "the current public Vercel/Render pair should be redeployed and checked before it is presented as a working "
            "live service."
        ],
    )
    add_doc_section(document, 24, "References")
    references = [
        "Project source, README, tests, deployment Blueprint and methodology, repository checkout (accessed 2026-10-10).",
        "European Space Agency / Copernicus, Sentinel-2 mission and MSI documentation: https://sentiwiki.copernicus.eu/web/s2-mission",
        "NASA Earth Observatory, Measuring Vegetation (NDVI overview): https://earthobservatory.nasa.gov/features/MeasuringVegetation",
        "McFeeters, S. K. (1996). The use of the Normalized Difference Water Index (NDWI) in the delineation of open water features. International Journal of Remote Sensing. https://doi.org/10.1080/01431169608948714",
        "Zha, Y., Gao, J., & Ni, S. (2003). Use of normalized difference built-up index in automatically mapping urban areas from TM imagery. International Journal of Remote Sensing. https://doi.org/10.1080/01431160304987",
        "Rasterio documentation: https://rasterio.readthedocs.io/en/stable/",
        "NumPy documentation: https://numpy.org/doc/stable/",
        "FastAPI documentation: https://fastapi.tiangolo.com/",
        "React documentation: https://react.dev/",
        "Vite documentation: https://vite.dev/guide/",
        "Supabase Auth documentation: https://supabase.com/docs/guides/auth",
        "Supabase Storage bucket access documentation: https://supabase.com/docs/guides/storage/buckets/fundamentals",
        "Alembic documentation: https://alembic.sqlalchemy.org/",
    ]
    add_doc_bullets(document, references)

    path = PACKAGE_DIR / "Satellite_Vision_Project_Report.docx"
    document.save(path)
    reopened = Document(path)
    if len(reopened.paragraphs) < 35 or len(reopened.tables) < 3:
        raise RuntimeError("The saved Word report did not pass its structural validation.")
    return path


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def add_background(slide) -> None:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, SlideInches(13.333), SlideInches(7.5)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(NAVY)
    shape.line.fill.background()


def add_text(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    size: float = 18,
    color: str = WHITE,
    bold: bool = False,
    align=PP_ALIGN.LEFT,
    font: str = "Aptos",
):
    box = slide.shapes.add_textbox(
        SlideInches(x), SlideInches(y), SlideInches(w), SlideInches(h)
    )
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = SlideInches(0.04)
    frame.margin_right = SlideInches(0.04)
    frame.margin_top = SlideInches(0.025)
    frame.margin_bottom = SlideInches(0.02)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_after = SlidePt(0)
    run = paragraph.add_run()
    run.text = text
    run.font.name = font
    run.font.size = SlidePt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return box


def add_card(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: str,
    accent: str = TEAL,
    title_size: float = 17,
    body_size: float = 12,
):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        SlideInches(x),
        SlideInches(y),
        SlideInches(w),
        SlideInches(h),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(NAVY_LIGHT)
    shape.line.color.rgb = rgb(accent)
    shape.line.width = SlidePt(1.2)
    add_text(slide, title, x + 0.15, y + 0.12, w - 0.3, 0.38, title_size, accent, True)
    add_text(
        slide,
        body,
        x + 0.15,
        y + 0.55,
        w - 0.3,
        h - 0.68,
        body_size,
        WHITE,
    )
    return shape


def add_slide_title(slide, title: str, kicker: str, subtitle: str | None = None) -> None:
    add_text(slide, kicker.upper(), 0.62, 0.30, 9.0, 0.28, 10, TEAL, True)
    add_text(slide, title, 0.62, 0.62, 12.0, 0.58, 27, WHITE, True)
    if subtitle:
        add_text(slide, subtitle, 0.66, 1.22, 12.0, 0.48, 12, MUTED)
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        SlideInches(0.65),
        SlideInches(7.15),
        SlideInches(12.0),
        SlideInches(0.012),
    )
    line.fill.solid()
    line.fill.fore_color.rgb = rgb(NAVY_LIGHT)
    line.line.fill.background()


def add_footer(slide, page: int) -> None:
    add_text(
        slide,
        f"SATELLITE VISION  /  PHASE 16  /  2026-10-10                                      {page:02d}",
        0.65,
        7.18,
        12.0,
        0.18,
        8,
        MUTED,
    )


def new_slide(presentation: Presentation, title: str, kicker: str, subtitle=None):
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    add_background(slide)
    add_slide_title(slide, title, kicker, subtitle)
    add_footer(slide, len(presentation.slides))
    return slide


def add_bullet_list(slide, items: list[str], x, y, w, h, size=17, color=WHITE):
    box = slide.shapes.add_textbox(
        SlideInches(x), SlideInches(y), SlideInches(w), SlideInches(h)
    )
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = SlideInches(0.12)
    frame.margin_right = SlideInches(0.08)
    for index, item in enumerate(items):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = item
        paragraph.level = 0
        paragraph.space_after = SlidePt(12)
        paragraph.font.name = "Aptos"
        paragraph.font.size = SlidePt(size)
        paragraph.font.color.rgb = rgb(color)
        paragraph._p.get_or_add_pPr().insert(0, OxmlElement("a:buChar"))
        paragraph._p.pPr[0].set("char", "•")
    return box


def add_flow_node(slide, x, y, w, h, title, body, accent=TEAL):
    add_card(slide, x, y, w, h, title, body, accent, 14, 10)


def build_presentation(demo: dict) -> Path:
    presentation = Presentation()
    presentation.slide_width = SlideInches(13.333)
    presentation.slide_height = SlideInches(7.5)
    presentation.core_properties.title = "Satellite Vision | Hackathon presentation"
    presentation.core_properties.subject = "Evidence-based satellite raster analysis prototype"
    presentation.core_properties.author = "Satellite Vision project team"
    presentation.core_properties.keywords = "satellite, geospatial, NDVI, hackathon"

    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    add_background(slide)
    accent = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        SlideInches(0.78),
        SlideInches(1.12),
        SlideInches(0.10),
        SlideInches(4.9),
    )
    accent.fill.solid()
    accent.fill.fore_color.rgb = rgb(TEAL)
    accent.line.fill.background()
    add_text(slide, "SATELLITE INTELLIGENCE  /  LAND MONITORING", 1.18, 1.04, 10.8, 0.35, 12, TEAL, True)
    add_text(slide, "SATELLITE\nVISION", 1.15, 1.72, 10.7, 2.12, 43, WHITE, True, font="Aptos Display")
    add_text(
        slide,
        "A transparent, local-first GeoTIFF analysis prototype",
        1.20,
        4.08,
        9.9,
        0.55,
        22,
        SKY,
    )
    add_text(slide, "Hackathon project  |  Evidence-based release review  |  10 October 2026", 1.2, 5.42, 10.8, 0.36, 12, MUTED)
    add_text(slide, "LOCAL SYNTHETIC DEMO VERIFIED  /  LIVE CLOUD NOT VERIFIED", 1.2, 6.1, 11.4, 0.42, 12, GOLD, True)
    add_footer(slide, 1)

    slide = new_slide(presentation, "The problem", "01 / CONTEXT", "Multispectral rasters are useful, but hard to validate and interpret without a disciplined workflow.")
    add_card(slide, 0.75, 2.05, 3.85, 2.65, "Raw data is technical", "Band files, nodata, grids and metadata make manual interpretation error-prone.", SKY)
    add_card(slide, 4.75, 2.05, 3.85, 2.65, "Indicators need context", "An index threshold is a screening signal, not a definitive land-cover or causal claim.", GOLD)
    add_card(slide, 8.75, 2.05, 3.85, 2.65, "Evidence matters", "Users need visible provenance, quality warnings and an honest insufficient-data state.", TEAL)
    add_text(slide, "Goal: turn compatible user-provided rasters into interpretable, reproducible outputs.", 1.0, 5.35, 11.3, 0.7, 22, WHITE, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "What is actually in this build?", "02 / SCOPE", "Implementation status is separated from cloud verification and future plans.")
    add_card(slide, 0.72, 1.98, 3.95, 3.7, "VERIFIED LOCALLY", "NDVI / NDWI / NDBI\nRule-based land-cover baseline\nAligned NDVI change\nSynthetic in-memory demo\nAutomated tests and frontend build", GREEN, 17, 14)
    add_card(slide, 4.72, 1.98, 3.95, 3.7, "CODE EXISTS; LIVE CHECK PENDING", "Supabase Auth checks\nOwner-scoped API access\nPostgreSQL migrations\nPrivate Storage artifact code\nConfigured Vercel / Render deployment", GOLD, 16, 13)
    add_card(slide, 8.72, 1.98, 3.95, 3.7, "INCOMPLETE / NOT VERIFIED", "Live scene search/download\nAOI clipping / reprojection\nTrained validated classifier\nDurable background queue\nLive cloud CRUD and user journey", RED, 16, 13)

    slide = new_slide(presentation, "System architecture", "03 / ARCHITECTURE", "Solid arrows show the normal local workflow; dashed labels are optional, not production-verified.")
    add_flow_node(slide, 0.78, 2.30, 2.45, 1.35, "Browser UI", "React + Vite\nMaps, charts, downloads", SKY)
    add_text(slide, ">", 3.25, 2.70, 0.42, 0.45, 23, TEAL, True, PP_ALIGN.CENTER)
    add_flow_node(slide, 3.75, 2.30, 2.55, 1.35, "FastAPI", "REST API\nvalidation + ownership", TEAL)
    add_text(slide, ">", 6.36, 2.70, 0.42, 0.45, 23, TEAL, True, PP_ALIGN.CENTER)
    add_flow_node(slide, 6.83, 2.30, 2.55, 1.35, "Raster pipeline", "Rasterio + NumPy\nindices + outputs", TEAL)
    add_text(slide, ">", 9.45, 2.70, 0.42, 0.45, 23, TEAL, True, PP_ALIGN.CENTER)
    add_flow_node(slide, 9.92, 2.30, 2.55, 1.35, "Local files", "GeoTIFF input\nlocal outputs", SKY)
    add_card(slide, 2.0, 4.55, 4.1, 1.25, "OPTIONAL: SUPABASE AUTH", "Backend verifies bearer with Auth user endpoint.", GOLD, 13, 10)
    add_card(slide, 7.2, 4.55, 4.1, 1.25, "OPTIONAL: POSTGRES + PRIVATE STORAGE", "Metadata and binary artifact paths.", GOLD, 13, 10)
    add_text(slide, "Production database, Auth and object storage were not verified.", 2.1, 6.12, 9.2, 0.38, 13, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Data flow and processing", "04 / PIPELINE", "Inputs must already be prepared and aligned; the application does not silently change their grid.")
    flow = [
        ("1  INPUT", "Single-band GeoTIFFs\nB02 / B03 / B04 / B08 / B11"),
        ("2  VALIDATE", "Required bands\nsize / CRS / transform"),
        ("3  MASK", "Nodata + finite values\nzero denominator excluded"),
        ("4  CALCULATE", "Indices / class rules\noptional historical difference"),
        ("5  DELIVER", "Statistics + GeoTIFF\nPNG overlay + JSON"),
    ]
    for index, (title, body) in enumerate(flow):
        x = 0.55 + index * 2.56
        add_flow_node(slide, x, 2.35, 2.23, 2.0, title, body, TEAL if index in (1, 3) else SKY)
        if index < len(flow) - 1:
            add_text(slide, ">", x + 2.24, 3.05, 0.31, 0.38, 18, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "Misaligned rasters, unsupported AOIs and missing bands produce explicit errors or unavailable states.", 0.95, 5.25, 11.4, 0.72, 19, WHITE, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Satellite-derived indices", "05 / METHODS", "Band ordering follows the code contract; an input filename/tag alone does not authenticate its sensor.")
    formulas = [
        ("NDVI", "B08 NIR + B04 Red", "(B08 - B04) / (B08 + B04)", "Vegetation greenness indicator"),
        ("McFeeters NDWI", "B03 Green + B08 NIR", "(B03 - B08) / (B03 + B08)", "Water-related indicator"),
        ("NDBI", "B11 SWIR + B08 NIR", "(B11 - B08) / (B11 + B08)", "Built-up indicator"),
    ]
    for index, (name, bands, formula, meaning) in enumerate(formulas):
        y = 1.95 + index * 1.48
        add_card(slide, 0.85, y, 3.05, 1.15, name, bands, TEAL, 15, 11)
        add_text(slide, formula, 4.2, y + 0.10, 4.5, 0.47, 20, WHITE, True)
        add_text(slide, meaning, 8.8, y + 0.13, 3.7, 0.58, 13, MUTED)
    add_text(slide, "Zero denominators / nodata are invalid; values are not silently clipped to [-1, 1].", 1.0, 6.45, 11.3, 0.35, 13, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Classification and change", "06 / RULES", "Interpretation is intentionally cautious: the active classes are transparent rules, not trained predictions.")
    add_card(slide, 0.8, 1.98, 5.75, 3.72, "CLASS PRECEDENCE", "1. Water: NDWI > 0.1\n2. Built-up: NDBI > 0.05\n3. Vegetation: NDVI >= 0.45\n4. Agriculture: NDVI >= 0.25\n5. Otherwise Bare land", TEAL, 17, 16)
    add_card(slide, 6.78, 1.98, 5.75, 3.72, "HISTORICAL DIFFERENCE", "NDVI change = current - historical\n\nRequires aligned grids and valid pixels.\n\nDescribes a paired-period difference; it does not establish cause.", SKY, 17, 16)
    add_text(slide, "No AOI clipping, automatic reprojection, atmospheric correction or cloud mask is implemented.", 1.0, 6.05, 11.3, 0.50, 15, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "AI/ML: what we can claim", "07 / MODEL VALIDATION", "A Random Forest wrapper exists, but is untrained and not used for the land-cover output.")
    add_card(slide, 0.8, 2.0, 3.78, 3.05, "ACTIVE METHOD", "Index-threshold baseline with five rule labels.", TEAL)
    add_card(slide, 4.78, 2.0, 3.78, 3.05, "TRAINING DATA", "No labeled training set or fitted model artifact is bundled.", GOLD)
    add_card(slide, 8.76, 2.0, 3.78, 3.05, "ACCURACY METRICS", "No supported held-out ground truth; accuracy / F1 cannot be claimed.", RED)
    add_text(slide, "Temporal forecast code uses time-ordered evaluation only when enough dated observations exist; current history is insufficient for independent validation.", 1.05, 5.62, 11.1, 0.76, 16, WHITE, True, PP_ALIGN.CENTER)

    ndvi = float(demo["results"]["ndvi"]["statistics"]["mean"])
    ndwi = float(demo["results"]["ndwi"]["statistics"]["mean"])
    ndbi = float(demo["results"]["ndbi"]["statistics"]["mean"])
    means = [("NDVI", ndvi, TEAL), ("NDWI", ndwi, SKY), ("NDBI", ndbi, GOLD)]
    slide = new_slide(presentation, "Dashboard example: synthetic only", "08 / ACTUAL LOCAL DEMO OUTPUT", "Values below were returned by the current local /api/geoai/demo endpoint; they are not satellite observations.")
    add_text(slide, "SYNTHETIC 4 x 4 FIXTURE  |  16 VALID PIXELS  |  NO DATE OR GEOGRAPHIC LOCATION", 0.85, 1.82, 11.7, 0.40, 12, GOLD, True, PP_ALIGN.CENTER)
    chart_left, chart_top, chart_width, zero_x = 2.05, 2.72, 7.4, 5.75
    zero = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, SlideInches(zero_x), SlideInches(chart_top - 0.12), SlideInches(0.018), SlideInches(2.95))
    zero.fill.solid()
    zero.fill.fore_color.rgb = rgb(MUTED)
    zero.line.fill.background()
    for index, (label, value, color) in enumerate(means):
        y = chart_top + index * 0.86
        add_text(slide, label, 1.0, y, 0.9, 0.34, 16, WHITE, True)
        width = max(abs(value) / 0.35 * (chart_width / 2), 0.04)
        left = zero_x - width if value < 0 else zero_x
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, SlideInches(left), SlideInches(y + 0.03), SlideInches(width), SlideInches(0.29))
        bar.fill.solid()
        bar.fill.fore_color.rgb = rgb(color)
        bar.line.fill.background()
        value_x = left - 0.95 if value < 0 else left + width + 0.08
        add_text(slide, f"{value:+.3f}", value_x, y - 0.01, 0.83, 0.35, 13, color, True, PP_ALIGN.CENTER)
    class_counts = demo["results"]["landcover"]["class_distribution"]
    add_card(
        slide,
        9.65,
        2.43,
        2.8,
        2.95,
        "RULE COUNTS",
        f"Built-up  {class_counts['4']['pixel_count']}\nVegetation  {class_counts['2']['pixel_count']}\nBare land  {class_counts['5']['pixel_count']}\nAgriculture  {class_counts['1']['pixel_count']}\nWater  {class_counts['3']['pixel_count']}",
        TEAL,
        14,
        12,
    )
    add_text(slide, "Synthetic reflectance-like inputs are uncalibrated. Counts are not real area or accuracy.", 1.0, 6.05, 11.5, 0.42, 13, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Database, storage and access", "09 / OPTIONAL CLOUD PATH", "The current source implements cloud paths; live Supabase verification was not possible.")
    add_card(slide, 0.78, 2.0, 3.75, 3.25, "SUPABASE AUTH", "Source validates bearer tokens server-side; owner identity scopes protected reads.", SKY)
    add_card(slide, 4.78, 2.0, 3.75, 3.25, "POSTGRESQL", "Analysis/job status, owner, JSON summary and artifact references; Alembic revisions.", TEAL)
    add_card(slide, 8.78, 2.0, 3.75, 3.25, "PRIVATE STORAGE", "Raster, image and report object bytes; signed access is short-lived where used.", GOLD)
    add_text(slide, "Mocked/local tests passed. Live CRUD, sign-in, upload and download were blocked; results GET accepted missing and malformed tokens.", 0.8, 5.83, 11.7, 0.55, 13, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "API and job lifecycle", "10 / BACKEND", "Analysis work runs synchronously inside the API request; it is not dispatched to a durable worker.")
    flow = [
        ("POST analysis", "Idempotency key\noptional"),
        ("queued", "Persisted transition\nwhen DB configured"),
        ("running", "Compute in request\nprocess"),
        ("completed / failed", "Persist status +\nartifact references"),
    ]
    for index, (title, body) in enumerate(flow):
        x = 0.88 + index * 3.12
        add_flow_node(slide, x, 2.5, 2.62, 1.65, title, body, TEAL if index in (0, 2) else SKY)
        if index < len(flow) - 1:
            add_text(slide, ">", x + 2.66, 3.05, 0.4, 0.4, 20, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "Process-local back-pressure and retry keys do not provide distributed scheduling or crash-resume.", 1.05, 5.35, 11.2, 0.60, 15, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Automated verification", "11 / TESTS", "Latest recorded local verification; tests are not live production checks.")
    metrics = [
        ("95", "backend tests passed", GREEN),
        ("28", "frontend tests passed", GREEN),
        ("PASS", "Vite production build", TEAL),
        ("200", "local synthetic demo HTTP", SKY),
    ]
    for index, (number, label, color) in enumerate(metrics):
        x = 0.78 + index * 3.15
        add_card(slide, x, 2.23, 2.78, 2.02, number, label, color, 25, 12)
    add_text(slide, "Backend: 0 failed, 3 warnings  |  Frontend: 0 failed  |  isolated storage/auth mocked  |  no live cloud CRUD", 0.95, 5.0, 11.5, 0.55, 15, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "Frontend npm audit (production dependencies): 0 vulnerabilities reported. Python advisory scan was not run.", 1.1, 5.86, 11.0, 0.52, 12, MUTED, False, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Performance: bounded local workload", "12 / PHASE 14 MEASUREMENT", "Windows 11 / Python 3.14.3; deterministic 512 x 512 synthetic rasters; one request per client.")
    headers = ["Clients", "HTTP 200 / 503", "All median / p95 ms", "Accepted median / p95 ms", "Sampled peak RSS"]
    rows = [
        ["1", "1 / 0", "632 / 632", "632 / 632", "240 MB"],
        ["5", "1 / 4", "50 / 1,774", "1,774 / 1,774", "253 MB"],
        ["10", "1 / 9", "75 / 928", "928 / 928", "250 MB"],
        ["25", "1 / 24", "154 / 232", "1,896 / 1,896", "259 MB"],
    ]
    x0, y0 = 0.72, 2.05
    widths = [1.2, 1.95, 2.25, 2.65, 2.25]
    for col, head in enumerate(headers):
        x = x0 + sum(widths[:col])
        add_text(slide, head, x, y0, widths[col] - 0.06, 0.76, 11, TEAL, True, PP_ALIGN.CENTER)
    for row_index, row in enumerate(rows):
        for col, value in enumerate(row):
            x = x0 + sum(widths[:col])
            add_text(slide, value, x, y0 + 0.82 + row_index * 0.67, widths[col] - 0.06, 0.45, 13, WHITE, col == 0, PP_ALIGN.CENTER)
    add_text(slide, "Attempt / accepted analyses per second: 1.42/1.42, 2.56/0.51, 9.74/0.97, 11.83/0.47.", 0.8, 5.62, 11.75, 0.38, 12, SKY, True, PP_ALIGN.CENTER)
    add_text(slide, "At 5/10/25 clients, 80%/90%/96% were rejected with HTTP 503. One local rerun only; no capacity claim.", 0.8, 6.05, 11.75, 0.42, 12, GOLD, True, PP_ALIGN.CENTER)
    add_text(slide, "No PostgreSQL, Storage, production latency, or multi-instance behavior was measured.", 0.9, 6.56, 11.5, 0.3, 10, MUTED, False, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Limitations and deployment reality", "13 / RELEASE READINESS", "A working build is not proof that the public deployment or cloud services are ready.")
    add_card(slide, 0.75, 1.95, 5.85, 3.75, "SCIENTIFIC / FUNCTIONAL GAPS", "No live scene retrieval\nNo AOI clipping or cloud mask\nNo supervised ground truth\nForecast history insufficient\nSource tags are unverified", GOLD, 16, 14)
    add_card(slide, 6.78, 1.95, 5.85, 3.75, "LIVE DEPLOYMENT CHECKS", "Old frontend bundle; no sign-in control\nAuth/persistence/demo routes: 404\nMissing and malformed token results GET: 200\nIdempotency-Key preflight: 400\nForecast probe: 500, no CORS header", RED, 16, 13)
    add_text(slide, "Redeploy matching builds; then verify CORS, Auth, DB, private Storage and one authenticated journey.", 1.15, 6.08, 11.0, 0.42, 14, WHITE, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Live demo: reliable local path", "14 / 5-MINUTE WALKTHROUGH", "Use the synthetic endpoint if no safe, prepared, aligned satellite test data is available.")
    steps = [
        "Start local FastAPI + Vite",
        "Open dashboard; click Demo mode",
        "Read synthetic indices + class rules",
        "Explain the supplied-GeoTIFF workflow",
        "Disclose cloud and science limits",
    ]
    for index, step in enumerate(steps):
        y = 1.9 + index * 0.86
        add_text(slide, f"{index + 1:02d}", 1.0, y, 0.75, 0.48, 20, TEAL, True, PP_ALIGN.CENTER)
        add_text(slide, step, 1.95, y, 9.8, 0.5, 18, WHITE, index == 1)
    add_text(slide, "Backup: local synthetic demo works without satellite-provider credentials or map tiles.", 1.05, 6.35, 11.2, 0.38, 13, GOLD, True, PP_ALIGN.CENTER)

    slide = new_slide(presentation, "Conclusion and references", "15 / TAKEAWAY", "A transparent software foundation; not yet a validated operational monitoring product.")
    add_text(slide, "Explainable calculations.\nExplicit data-quality limits.\nReproducible local demo.\nHonest cloud-verification boundary.", 0.95, 2.05, 5.8, 2.5, 23, WHITE, True)
    add_card(slide, 7.05, 1.95, 5.2, 3.7, "SELECTED SOURCES", "Copernicus Sentinel-2 mission: sentiwiki.copernicus.eu/web/s2-mission\n\nMcFeeters NDWI (1996): doi.org/10.1080/01431169608948714\n\nZha et al. NDBI (2003): doi.org/10.1080/01431160304987\n\nNASA NDVI: earthobservatory.nasa.gov/features/MeasuringVegetation", TEAL, 15, 11)
    add_text(slide, "Next: redeploy and verify cloud journey; validate against documented scenes and independent ground truth.", 0.9, 6.15, 11.5, 0.55, 14, SKY, True, PP_ALIGN.CENTER)

    path = PACKAGE_DIR / "Satellite_Vision_Hackathon_Presentation.pptx"
    presentation.save(path)
    reopened = Presentation(path)
    if len(reopened.slides) != 16:
        raise RuntimeError(f"Expected 16 slides, found {len(reopened.slides)}.")
    return path


def main() -> None:
    demo = get_demo_output()
    report_path = build_report(demo)
    presentation_path = build_presentation(demo)
    print(f"Synthetic demo scenario: {demo['scenario_id']} (data_classification={demo['data_classification']})")
    print(f"Created editable Word report: {report_path}")
    print(f"Created editable PowerPoint deck: {presentation_path}")
    print("Validation: reopened DOCX, reopened PPTX, and confirmed 16 editable slides.")


if __name__ == "__main__":
    main()
