# Satellite Vision — Final Test Results

**Run date:** 2026-10-10  
**Scope:** Local tests/builds, local API startup, dependency checks, and read-only checks against the configured public deployment.  
**Classification:** `PASS` = observed success for that exact check; `FAIL` = observed contract/behavior failure; `BLOCKED` = required credential/service/data was unavailable; `NOT TESTED` = no check executed. A passing HTTP health probe is not proof of cloud persistence.

## Latest rerun and live recheck — 2026-10-10 (supersedes earlier snapshots)

### Local automated checks

| Check | Exact command / evidence | Result |
|---|---|---|
| Backend tests | From `satellite-intelligence/backend`: `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pytest -q` | **PASS** — 108 passed, 0 failed, 3 warnings in 13.78s. Warnings are Starlette/httpx and Alembic deprecations. |
| Frontend tests | From `satellite-intelligence/frontend`: `npm test -- --run` | **PASS** — 3 files, 28 tests passed in 5.25s. |
| Frontend production build | `npm run build` | **PASS, LOCAL ONLY** — Vite transformed 806 modules and emitted `dist/assets/index-BOqK-m93.js`. The emitted entry asset matches the one observed from the live frontend. |
| NPM advisory audit | `npm audit --audit-level=high` | **PASS** — 0 vulnerabilities. |
| Python dependency consistency | `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pip check` | **PASS** — no broken requirements found. |
| Python syntax | `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m compileall -q 'E:\project\Hackathon Erode\satellite-intelligence\backend'` | **PASS** — exit code 0. |
| Git whitespace | `git diff --check` and `git diff --cached --check` | **PASS** — exit code 0; Git emitted line-ending conversion warnings for generated frontend assets. |
| Python advisory audit | `pip-audit` availability check | **NOT TESTED** — `pip-audit` is not installed; no Python vulnerability advisory scan is claimed. |
| Frontend bundle backend-secret scan | `rg -l 'SUPABASE_SERVICE_ROLE_KEY|DATABASE_URL' satellite-intelligence/frontend/dist` | **PASS, LIMITED** — neither backend-only variable name was found. This scan does not substitute for Vercel project-setting inspection. |

### Live, read-only checks

The URLs below are the configured public URLs. Their dashboard root/branch/host mapping and exact deployed commit were not available for independent confirmation.

| Request / observation | Result | Interpretation |
|---|---:|---|
| `GET https://team-s4-ten.vercel.app/` | **200** | Frontend is reachable and references `/assets/index-BOqK-m93.js`. |
| Browser-rendered sign-in state | **“Authentication setup required”** | **FAIL:** current frontend bundle lacks usable Supabase Auth configuration; it requests `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. |
| Browser request to `https://satellite-vision-dashboard.onrender.com/api/auth/status` | **200** | Frontend reaches the configured API origin. |
| `GET /api/health` on configured API | **200** | Reachability only. |
| `GET /api/ready` on configured API | **503** | Public checks: `configuration=unavailable`, `database=ok`, `storage=unavailable`. The DB connectivity check passed; this is not metadata CRUD evidence. |
| `GET /api/auth/status` on configured API | **200** | Safe boolean fields: `authentication_required=true`, `supabase_auth_configured=false`. |
| `GET /api/results?limit=1`, no token | **401** | **PASS:** anonymous request rejected. Response body was not inspected. |
| Same results request with invalid audit bearer | **503** | **FAIL-CLOSED, BLOCKED:** no results were returned; expected 401 behavior could not be confirmed because backend Auth is unconfigured/unavailable. |
| `GET /api/persistence/status` and `/api/geoai/demo`, no token | **401** each | **PASS:** requests are blocked before access. Protected handler registration cannot be proven without valid auth. |
| Trusted-origin `OPTIONS /api/analyze/all`, POST with `authorization,content-type,idempotency-key` | **200**; allow-origin matched Vercel and allow-headers included `Idempotency-Key` | **PASS:** current preflight behavior. |
| Same preflight from `https://untrusted.example` | **400**; no allow-origin | **PASS:** untrusted origin rejected. |
| Live Supabase Auth/JWT session and two-user isolation | Not run | **BLOCKED:** Auth status reports unconfigured; no dedicated test identities or valid session. |
| Live database metadata CRUD | Not run | **BLOCKED:** readiness DB connection check is `ok`, but no authorized CRUD operation was performed. |
| Private Storage policy and object round trip | Not run | **BLOCKED:** readiness reports storage unavailable; no cloud object was created. |
| Complete authenticated satellite-analysis workflow | Not run | **BLOCKED:** Auth/Storage prerequisites and dedicated test identity/raster unavailable. |
| Vercel/Render dashboard project settings | Not inspected | **BLOCKED:** authorized platform dashboard session unavailable. |

### Git/release state at this rerun

Branch `main` and `origin/main` both pointed to `7daa30a31aa8788c84a6278e27377cc78999b056` before the report update. The application worktree was clean then. The commit contains 79 changed paths (9,764 additions and 527 deletions), so it is not a minimal security-only patch. This audit did not stage, commit, push, or deploy. The later live frontend bundle matches the local committed build artifact, but no deployment SHA was exposed by either service. After writing the reports, exactly these three report files are modified and unstaged; no paths are staged.

## Follow-up verification — 2026-10-10

### Local regression rerun

| Check | Command / observation | Result |
|---|---|---|
| Backend suite, including new private-route and CORS-rejection regressions | From `satellite-intelligence/backend`: `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pytest -q` | **PASS** — 98 passed, 0 failed, 3 warnings in 10.65s. |
| Frontend tests | From `satellite-intelligence/frontend`: `npm test -- --run` | **PASS** — 3 files, 28 tests. |
| Production frontend bundle | `npm run build` | **PASS** — Vite transformed 806 modules and emitted `/assets/index-BOqK-m93.js`. The built bundle contains the AuthPanel copy/client and the explicit missing-auth-configuration state. |
| Frontend dependency audit | `npm audit --audit-level=high` | **PASS** — 0 vulnerabilities. |
| Python dependency and syntax checks | `python -m pip check`; `python -m compileall -q satellite-intelligence\backend` using the configured project interpreter | **PASS** — no broken requirements or syntax errors. |
| Results/analysis authorization | Covered by expanded `test_supabase_authentication_and_analysis_artifact_ownership` and `test_authenticated_middleware_protects_api_routes_and_preserves_cors` | **PASS, MOCKED** — missing and invalid credentials receive 401 on nine results/analysis/artifact GET paths and the history route; two mocked identities see only their own result-list/detail/job/artifact/download/image data. No live Supabase Auth calls were made. |
| CORS preflight regression | Expanded `test_cors_preflight_rejects_untrusted_origins_methods_and_headers` | **PASS, LOCAL** — rejects untrusted origins, `PUT`, and an unapproved request header with HTTP 400. The allowed-origin POST with `Idempotency-Key` is covered separately and passes HTTP 200. |
| Python dependency audit (`pip-audit`) | Not run | **NOT TESTED** — tool is unavailable. |
| Audit report consistency | Verified all three reports are non-empty, local links resolve, code fences balance, and evidence/credential-pattern checks pass; `git diff --check` passes. | **PASS** — documentation validation only. |

The VS Code test-runner adapter did not discover Python tests; the project suite was executed directly with the configured virtual-environment interpreter using `pytest -q`.

### Deployment revision and bundle comparison

| Check | Evidence | Result |
|---|---|---|
| Latest GitHub `main` / local `HEAD` | SHA `fd07461b300357d6d58d7338a7d434e047631884`; latest commit message is `fix(cors): always allow verified production frontend`. | **PASS: repository revision identified**; not proof of the active deployment commit. |
| Deployed Vercel JS | Live HTML uses `/assets/index-BEv36dup.js`; the public bundle did not contain the sign-in text or a `supabase.co` hostname marker. `git show HEAD:satellite-intelligence/frontend/dist/index.html` references the same legacy asset. | **FAIL: stale frontend**. |
| Current working-tree Vite build | Build output uses `/assets/index-BOqK-m93.js`; AuthPanel and Supabase Auth code are present. | **PASS: local build only**, not deployed. |
| Vercel API target | Checked-in `frontend/.env.production` and the live page's API resource origin both use `https://satellite-vision-dashboard.onrender.com`. | **PASS: configuration matches observed origin**; **BLOCKED: Render dashboard hostname mapping** because account settings are inaccessible and `render.yaml` service name differs. |
| Actual Vercel/Render deployment SHA and dashboard build/root settings | No commit identifier was visible in the inspected public page/API responses; Vercel/Render dashboards require sign-in; no CLI/token or repository deployment workflow was available. | **BLOCKED** — exact deployed commit, root override, environment settings, and deployment trigger are not verifiable. |
| Vercel Supabase build variables | `frontend/.env.production` contains only `VITE_API_URL`; actual Vercel settings are not accessible. | **BLOCKED** — sign-in controls require `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` to be configured at build time. Never use a service-role key here. |

### Read-only live recheck

These requests used only the configured public URLs. Response bodies from results-list checks were directed to `NUL` and not inspected.

| Request | HTTP result | Classification |
|---|---:|---|
| `GET https://team-s4-ten.vercel.app/` | 200 | **PASS: availability; FAIL: current-build parity** |
| `GET https://satellite-vision-dashboard.onrender.com/api/health` | 200 | **PASS: reachability only** |
| `GET https://satellite-vision-dashboard.onrender.com/api/ready` | 200 | **PASS: reachability only** |
| `GET` `/api/auth/status`, `/api/persistence/status`, `/api/geoai/demo` on the configured API | 404 each | **FAIL: route parity** |
| `GET /api/results?limit=1`, no authorization | 200 | **FAIL: authorization behavior**; 29-byte body discarded. |
| Same results GET with deliberately invalid test bearer | 200 | **FAIL: authorization behavior**; body discarded and not inspected. |
| `OPTIONS /api/analyze/all` from `https://team-s4-ten.vercel.app`, requested `POST`, headers `authorization,content-type,idempotency-key` | 400; `Access-Control-Allow-Origin` matched the Vercel origin; `Access-Control-Allow-Headers` omitted `Idempotency-Key` | **FAIL: deployed CORS preflight**. |

### Cloud workflow verification

| Check | Result | Reason |
|---|---|---|
| Live PostgreSQL connection / safe transaction | **BLOCKED** | `DATABASE_URL` absent. |
| Live Supabase Auth/JWT and two-user isolation | **BLOCKED** | Supabase URL/keys and authorized test users absent; the live auth-status route is 404. |
| Private Storage bucket/policy and harmless object round trip | **BLOCKED** | Storage credentials absent; no object was created. Source unit tests verify private-bucket enforcement with mocks only. |
| Complete authorized analysis with persisted artifacts | **BLOCKED** | No approved test raster or cloud credentials; deployed demo route is 404. Local generated/synthetic raster tests are not a live workflow. |
| RLS/grants | **BLOCKED / NOT IMPLEMENTED IN REPOSITORY MIGRATIONS** | No policy declarations found; no database access to query actual Supabase settings. |

## Local automated tests and builds

| ID | Command / test | Result | Evidence |
|---|---|---|---|
| BE-01 | From `satellite-intelligence/backend`: `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pytest -q` | **PASS** | `95 passed, 0 failed, 3 warnings in 22.72s`. |
| FE-01 | From `satellite-intelligence/frontend`: `npm test -- --run` | **PASS** | Vitest: 3 test files passed; 28 tests passed. |
| FE-02 | From `satellite-intelligence/frontend`: `npm run build` | **PASS** | Vite transformed 806 modules and completed the production build. |
| SEC-01 | From `satellite-intelligence/frontend`: `npm audit --audit-level=high` | **PASS** | `found 0 vulnerabilities`. |
| PY-01 | `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pip check` | **PASS** | `No broken requirements found.` |
| PY-02 | From repository root: `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m compileall -q satellite-intelligence\backend` | **PASS** | Exit code 0; no syntax compilation errors. |
| REG-01 | Focused tests: auth middleware/CORS behavior, malformed-token rejection, mock Supabase auth/artifact ownership, idempotency CORS, synthetic GeoTIFF output, mocked artifact upload/retrieval, and deterministic demo | **PASS** | Step 2 focused rerun: 7 passed, 1 warning. These are local tests; cloud HTTP and Storage were mocked. |
| DB-01 | Full suite migration-preservation coverage, including `test_owner_migration_preserves_legacy_rows_without_assigning_an_owner` | **PASS, LOCAL ONLY** | Migration behavior was tested against local test data; no production migration was run. |

The full backend suite warnings were one Starlette/httpx TestClient deprecation and two Alembic configuration deprecations. They did not fail tests.

## Local API execution

The audit started Uvicorn as a child process bound to `127.0.0.1` on port `18741`, queried it, then stopped the exact PID started by the audit.

| Request | Result | Sanitized evidence |
|---|---|---|
| `GET http://127.0.0.1:18741/api/health` | **PASS** | `status=ok`, service `satellite-intelligence-api`. |
| `GET http://127.0.0.1:18741/api/ready` | **PASS, LOCAL CONFIGURATION ONLY** | `status=ok`; configuration `ok`, database/storage `not_required` because no cloud settings were configured. |
| `GET http://127.0.0.1:18741/api/auth/status` | **PASS, LOCAL MODE ONLY** | Auth not required and Supabase Auth not configured locally. This is expected local/demo configuration, not a protected-cloud test. |
| `GET http://127.0.0.1:18741/api/geoai/demo` | **PASS** | `data_classification=synthetic`, scenario `synthetic-index-baseline-v1`. No file or user result was created by the fixture. |
| `GET http://127.0.0.1:18741/api/dataset` | **PASS, EMPTY INPUT STATE** | Endpoint returned success; no bundled GeoTIFF dataset was available. |

## Live frontend and API checks

**Configured public frontend:** `https://team-s4-ten.vercel.app/`  
**Configured frontend API origin:** `https://satellite-vision-dashboard.onrender.com` from `frontend/.env.production`.  
The public frontend page loaded resources from that API origin. `render.yaml` declares Render service name `satellite-vision-api`; no authenticated Dashboard access was available to confirm the service-to-host mapping.

| ID | Command / check | Result | Evidence |
|---|---|---|---|
| LIVE-01 | Browser open/inspection of `https://team-s4-ten.vercel.app/` | **PASS: availability; FAIL: current build parity** | Page title loaded; module was `/assets/index-BEv36dup.js`. No visible sign-in control was found. |
| LIVE-02 | `curl.exe --silent --show-error --max-time 60 -o NUL -w '%{http_code}' https://satellite-vision-dashboard.onrender.com/api/health` | **PASS: reachability only** | HTTP 200 after a longer retry. Does not establish DB/Auth/Storage operation. |
| LIVE-03 | Same bounded GET for `/api/ready` | **PASS: reachability only** | HTTP 200 after retry. No live database write/read or Storage operation was performed. |
| LIVE-04 | GET `/api/auth/status` on configured API | **FAIL: route parity** | HTTP 404; route exists in current repository source. |
| LIVE-05 | GET `/api/persistence/status` on configured API | **FAIL: route parity** | HTTP 404; route exists in current repository source. |
| LIVE-06 | GET `/api/geoai/demo` on configured API | **FAIL: route parity** | HTTP 404; route exists in current repository source. |
| LIVE-07 | GET `/api/results?limit=1`, without `Authorization`, response redirected to `NUL` | **FAIL: auth behavior** | HTTP 200, 29 bytes. Body discarded and not inspected. |
| LIVE-08 | Same GET with a deliberately malformed test bearer value, response redirected to `NUL` | **FAIL: auth behavior** | HTTP 200, 29 bytes. Body discarded and not inspected; no real credential was used. |
| LIVE-09 | `OPTIONS /api/analyze/all`, origin `https://team-s4-ten.vercel.app`, requested headers `authorization,content-type,idempotency-key` | **FAIL: CORS behavior** | HTTP 400; `Access-Control-Allow-Headers` omitted `Idempotency-Key`. |
| LIVE-10 | Browser resource-origin check | **PASS: target agreement** | Vercel page loaded a resource from `https://satellite-vision-dashboard.onrender.com`, matching the checked-in Vite production setting. It does not prove this is the intended active Render service. |

The anonymous/malformed credential checks were read-only. Only status and byte count were collected; response content was not fetched for inspection or included in logs.

## Database, Auth, Storage and full workflow

| Check | Result | Exact limitation |
|---|---|---|
| Supabase PostgreSQL connection and reversible test transaction | **BLOCKED** | `DATABASE_URL` was absent. No production SQL transaction or test record was attempted. |
| Real Supabase Auth session and backend JWT validation | **BLOCKED** | No authorized test credentials; deployed auth-status endpoint returned 404. |
| Real private Storage object upload/download and cleanup | **BLOCKED** | `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` were absent. No object was created; no cleanup was needed. |
| Real cross-user artifact authorization | **BLOCKED** | No cloud account credentials or separate test users. |
| End-to-end deployed analysis and artifact retrieval | **BLOCKED** | Current demo route returned 404; no test GeoTIFF was bundled; no user-authorized dataset or cloud credentials. |
| Live satellite-provider query | **NOT TESTED / INCOMPLETE FEATURE** | The checked-in provider defaults to local; live scene search/download is not operational. Provider credentials were absent. |
| Model accuracy/ground-truth evaluation | **NOT TESTED / NO CLAIM** | No labeled independent satellite dataset or trained active classifier is supplied. The active land-cover implementation is threshold-based. |

## Tools/checks not available or not defined

| Check | Result | Reason |
|---|---|---|
| `npm run lint` / frontend typecheck | **NOT CONFIGURED** | `package.json` only defines `dev`, `build`, and `test`; frontend source is JS/JSX, not TypeScript. |
| `pip-audit` | **NOT TESTED** | The command was unavailable. No new Python audit package was installed. |
| Standalone `ruff`, `mypy`, `eslint`, `tsc`, `gitleaks` | **NOT TESTED** | Not available in the environment. |
| Mermaid rendering engine | **NOT TESTED** | No Mermaid package was installed. Architecture Markdown code fences and six diagram declarations were structurally checked elsewhere; this is not a renderer result. |
| Genuine satellite screenshots / production result images | **BLOCKED / NOT AVAILABLE** | No authorized real-scene data or verified production outputs were available. No synthetic chart or image is represented as a satellite screenshot. |
| Production load/stress test | **NOT RUN** | No uncontrolled production traffic was generated. A prior bounded local synthetic benchmark is documented in the project README; it is not a current production-capacity measurement. |

## Reproduction commands

Backend:

```powershell
Set-Location 'E:\project\Hackathon Erode\satellite-intelligence\backend'
& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pytest -q
```

Frontend:

```powershell
Set-Location 'E:\project\Hackathon Erode\satellite-intelligence\frontend'
npm test -- --run
npm run build
npm audit --audit-level=high
```

Dependency/syntax checks:

```powershell
& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pip check
& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m compileall -q 'E:\project\Hackathon Erode\satellite-intelligence\backend'
```

## Release-preparation verification — 2026-10-10

This is the latest local verification. No deployment was performed, so the live results above remain the last observed public-service evidence, not a post-deployment retest.

| Check | Exact command / observation | Result |
|---|---|---|
| Git branch/revision | `git branch --show-current`; `git rev-parse HEAD`; `git rev-parse origin/main` | **PASS: state identified** — `main`; local and `origin/main` both `fd07461b300357d6d58d7338a7d434e047631884`. |
| Git worktree/release hygiene | `git status --porcelain=v1`; `git diff --cached --name-only` | **BLOCKED: not release-clean** — 41 tracked paths changed (39 modifications, 2 deletions), 19 untracked status entries, 0 staged. No changes were staged or discarded. |
| Backend suite | From `satellite-intelligence/backend`: `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pytest -q` | **PASS** — 98 passed, 0 failed, 3 warnings in 12.19s. The warnings are TestClient/Alembic deprecations. |
| Frontend tests | From `satellite-intelligence/frontend`: `npm test -- --run` | **PASS** — 3 files, 28 tests passed in 4.28s. |
| Frontend production build | From `satellite-intelligence/frontend`: `npm run build` | **PASS, LOCAL ONLY** — Vite transformed 806 modules and generated `dist/index.html` plus `/assets/index-BOqK-m93.js`; the generated bundle contains the AuthPanel. |
| Frontend dependency audit | From `satellite-intelligence/frontend`: `npm audit --audit-level=high` | **PASS** — 0 vulnerabilities. |
| Python dependency consistency | `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m pip check` | **PASS** — no broken requirements. |
| Backend syntax | `& 'E:\project\Hackathon Erode\.venv\Scripts\python.exe' -m compileall -q 'E:\project\Hackathon Erode\satellite-intelligence\backend'` | **PASS** — exit code 0. |
| Whitespace/conflict-marker check | `git diff --check` | **PASS** — no whitespace errors; Git emitted only a line-ending conversion warning for `backend/requirements.txt`. |
| Vercel/Render CLI and credential availability | `Get-Command vercel`, `Get-Command render`, and names-only process-environment presence checks | **BLOCKED** — both CLIs and both deployment tokens were absent. `DATABASE_URL` and Supabase URL/keys were also absent locally. No values were printed. |
| Platform project/service settings | Read repository manifests only; platform account pages require sign-in | **BLOCKED** — Vercel root/build/output/branch/variables and Render active root/branch/commit/hostname/secrets cannot be confirmed from this environment. |
| API hostname reconciliation | Compare `frontend/.env.production` with `render.yaml` | **BLOCKED** — configured frontend host is `satellite-vision-dashboard.onrender.com`; Blueprint name is `satellite-vision-api`; no replacement was guessed. |
| Post-deployment live checks | No approved deployment occurred | **NOT RUN** — the previous live checks remain failures/blocks as tabulated above; local tests do not clear them. |

### Release candidate scope

The current auth, API, storage, migration, frontend sign-in, and regression-test paths are enumerated in the “Candidate implementation file set” section of [FINAL_AUDIT_REPORT.md](FINAL_AUDIT_REPORT.md). That candidate was not staged: extensive diffs in core route/application files and modified imported processing/GeoAI/provider dependencies prevent certifying a minimal release from filenames alone. Generated `frontend/dist` output was not included as a release artifact pending Vercel build-setting confirmation.

**Pre-release verdict: BLOCKED.** Local quality checks pass, but platform settings, hostname, production environment variables, live authorization, CORS, and Supabase integration remain unresolved. No production data was touched.
