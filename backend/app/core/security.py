"""Who is calling: verifies the professor's Supabase access token.

The browser sends its Supabase session token with every request:

    Authorization: Bearer <access token>

The token is verified locally against the Supabase project's public signing keys
(JWKS), so no secret is needed. The professor's id always comes from the
verified token, never from anything else the browser sends.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

import httpx2
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import AppError, AuthenticationError

ALLOWED_ALGORITHMS = {"ES256", "RS256", "EdDSA"}
JWKS_TTL_SECONDS = 600
# A token signed with an unknown key may mean the keys rotated; re-fetch, but not too often.
JWKS_MIN_REFRESH_SECONDS = 30


@dataclass(frozen=True)
class CurrentProfessor:
    """The signed-in professor making this request."""

    id: str
    email: str | None
    # Used to call Supabase as this professor, so Row Level Security applies.
    access_token: str = field(repr=False)


class JwksProvider:
    """Fetches and caches the Supabase project's public signing keys."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        now = time.monotonic()
        if kid not in self._keys or now - self._fetched_at > JWKS_TTL_SECONDS:
            async with self._lock:
                stale = now - self._fetched_at > JWKS_TTL_SECONDS
                may_refresh = now - self._fetched_at > JWKS_MIN_REFRESH_SECONDS
                if stale or (kid not in self._keys and may_refresh):
                    await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError()
        return key

    async def _refresh(self) -> None:
        try:
            async with httpx2.AsyncClient(timeout=10) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                jwks = response.json()
        except Exception as error:  # network problems, bad JSON
            if self._keys:
                return  # keep using the keys we have
            raise AppError(
                "Sign-in can't be verified right now. Please try again.",
                code="auth_unavailable",
            ) from error
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in jwks.get("keys", []):
            if jwk.get("use", "sig") != "sig" or "kid" not in jwk:
                continue
            try:
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
            except jwt.PyJWKError:
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()


_jwks_provider: JwksProvider | None = None


def get_jwks_provider() -> JwksProvider:
    global _jwks_provider
    if _jwks_provider is None:
        if not settings.is_supabase_configured:
            raise AppError(
                "The API isn't connected to Supabase. Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.",
                code="not_configured",
            )
        _jwks_provider = JwksProvider(settings.supabase_jwks_url)
    return _jwks_provider


async def verify_access_token(token: str, provider: JwksProvider) -> CurrentProfessor:
    """Returns the professor the token belongs to, or raises AuthenticationError."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise AuthenticationError() from error
    algorithm = header.get("alg")
    kid = header.get("kid")
    if algorithm not in ALLOWED_ALGORITHMS or not kid:
        raise AuthenticationError()

    key = await provider.get_key(kid)
    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=settings.supabase_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=10,
        )
    except jwt.PyJWTError as error:
        raise AuthenticationError() from error

    # Only real, signed-in professors: not the anon role, not anonymous sign-ins.
    if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
        raise AuthenticationError()
    subject = claims.get("sub")
    try:
        professor_id = str(uuid.UUID(str(subject)))
    except ValueError as error:
        raise AuthenticationError() from error

    email = claims.get("email")
    return CurrentProfessor(
        id=professor_id,
        email=email if isinstance(email, str) else None,
        access_token=token,
    )


_bearer = HTTPBearer(auto_error=False)


async def get_current_professor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentProfessor:
    """FastAPI dependency for every professor-specific endpoint."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthenticationError("Please log in to use ProfPilot's AI features.")
    return await verify_access_token(credentials.credentials, get_jwks_provider())
