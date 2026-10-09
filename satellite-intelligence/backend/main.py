import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import router
from config import settings, validate_runtime_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("satellite-intelligence")

startup_issues = validate_runtime_settings()
if startup_issues:
    logger.warning("Configuration validation warnings: %s", startup_issues)

app = FastAPI(
    title="Satellite Intelligence API",
    version="1.0.0",
    description="Stage-A satellite intelligence pipeline with safe local/live data handling.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(router, prefix="/api")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        return response
    except Exception:
        logger.exception("Unhandled error for %s %s", request.method, request.url.path)
        raise


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": "Request validation failed.", "errors": exc.errors()})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("Internal processing error for %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Analysis could not be completed because of an internal processing error."},
    )


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "Satellite Intelligence API", "health": "/api/health", "ready": "/api/ready"}


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    issues = validate_runtime_settings()
    return {
        "status": "ok" if not issues else "degraded",
        "checks": {"configuration": issues},
        "service": "satellite-intelligence-api",
        "stage": "A",
    }
