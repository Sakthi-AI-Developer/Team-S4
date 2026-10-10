from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import is_valid_supabase_url, settings

bearer_scheme = HTTPBearer(auto_error=False)
authenticated_user_id: ContextVar[str | None] = ContextVar(
    "authenticated_user_id", default=None
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None = None


def verify_access_token(access_token: str) -> AuthenticatedUser:
    if not access_token or len(access_token) > 8192:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The Supabase sign-in session is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    supabase_url = settings.supabase_url
    if (
        not supabase_url
        or not is_valid_supabase_url(supabase_url)
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Authentication requires SUPABASE_URL to be a valid HTTPS "
                "Supabase project URL."
            ),
        )
    if not settings.supabase_anon_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication requires SUPABASE_ANON_KEY on the backend.",
        )

    try:
        response = httpx.get(
            f"{supabase_url.rstrip('/')}/auth/v1/user",
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=8,
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The Supabase sign-in session is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        )
    try:
        user = response.json()
        user_id = user["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service returned an invalid response.",
        ) from exc
    if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service returned an invalid user identity.",
        )
    email = user.get("email")
    return AuthenticatedUser(user_id, email if isinstance(email, str) else None)


def current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> AuthenticatedUser | None:
    if not settings.authentication_required:
        return None
    cached_user = getattr(request.state, "authenticated_user", None)
    if isinstance(cached_user, AuthenticatedUser):
        return cached_user
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid Supabase sign-in is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_access_token(credentials.credentials)
