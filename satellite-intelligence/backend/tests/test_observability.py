import json
import logging

from fastapi.testclient import TestClient
import pytest

import main


def test_cors_preflight_allows_idempotency_key_for_analysis_requests():
    origin = main.settings.cors_origins[0]
    response = TestClient(main.app).options(
        "/api/analyze/all",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "idempotency-key" in response.headers[
        "access-control-allow-headers"
    ].lower()


def test_production_frontend_preflight_allows_idempotency_key():
    origin = "https://team-s4-ten.vercel.app"
    assert origin in main.settings.cors_origins
    response = TestClient(main.app).options(
        "/api/analyze/all",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "idempotency-key" in response.headers[
        "access-control-allow-headers"
    ].lower()


@pytest.mark.parametrize(
    ("origin", "method", "requested_headers", "allow_origin"),
    [
        (
            "https://untrusted.example.test",
            "POST",
            "authorization,content-type,idempotency-key",
            False,
        ),
        (
            "https://team-s4-ten.vercel.app",
            "PUT",
            "authorization,content-type,idempotency-key",
            True,
        ),
        (
            "https://team-s4-ten.vercel.app",
            "POST",
            "authorization,content-type,x-untrusted-header",
            True,
        ),
    ],
)
def test_cors_preflight_rejects_untrusted_origins_methods_and_headers(
    origin, method, requested_headers, allow_origin
):
    response = TestClient(main.app).options(
        "/api/analyze/all",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": requested_headers,
        },
    )

    assert response.status_code == 400
    if allow_origin:
        assert response.headers["access-control-allow-origin"] == origin
    else:
        assert "access-control-allow-origin" not in response.headers


def test_application_logs_are_structured_and_correlated_without_request_secrets():
    context_token = main.request_id_context.set("request-123")
    try:
        record = logging.LogRecord(
            name="satellite-intelligence",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Analysis job failed.",
            args=(),
            exc_info=None,
        )
        record.event = "analysis.job_failed"
        record.error_type = "RuntimeError"
        formatted = json.loads(main.JsonLogFormatter().format(record))
    finally:
        main.request_id_context.reset(context_token)

    assert formatted["request_id"] == "request-123"
    assert formatted["severity"] == "ERROR"
    assert formatted["event"] == "analysis.job_failed"
    assert formatted["error_type"] == "RuntimeError"
    assert formatted["timestamp"].endswith("+00:00")
    assert "token" not in formatted
    assert "password" not in formatted
