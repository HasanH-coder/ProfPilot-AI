"""Verifying the professor's Supabase access token (ES256, against the project's public keys)."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.core.errors import AuthenticationError
from app.core.security import JwksProvider, verify_access_token
from app.main import app

ISSUER_URL = "https://example-project.supabase.co"
KID = "test-key"


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", ISSUER_URL)
    monkeypatch.setattr(settings, "supabase_publishable_key", "sb_publishable_test")
    settings.__dict__.pop("supabase_issuer", None)
    settings.__dict__.pop("supabase_jwks_url", None)
    private_key = ec.generate_private_key(ec.SECP256R1())
    provider = JwksProvider("unused")
    public_jwk = jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    provider._keys = {KID: jwt.PyJWK({**public_jwk, "kid": KID, "alg": "ES256", "use": "sig"})}
    provider._fetched_at = time.monotonic()
    monkeypatch.setattr(security, "_jwks_provider", provider)
    yield private_key, provider
    settings.__dict__.pop("supabase_issuer", None)
    settings.__dict__.pop("supabase_jwks_url", None)


def make_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": "prof@example.edu",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": f"{ISSUER_URL}/auth/v1",
        "iat": now,
        "exp": now + 3600,
        "is_anonymous": False,
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": KID})


@pytest.mark.anyio
async def test_valid_token_identifies_the_professor_from_sub(signing_key):
    private_key, provider = signing_key
    subject = str(uuid.uuid4())
    professor = await verify_access_token(make_token(private_key, sub=subject), provider)
    assert professor.id == subject
    assert professor.email == "prof@example.edu"
    assert "access_token" not in repr(professor)  # the token never appears in logs


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 60},
        {"aud": "anon"},
        {"iss": "https://other-project.supabase.co/auth/v1"},
        {"role": "anon"},
        {"is_anonymous": True},
        {"sub": "not-a-uuid"},
    ],
    ids=["expired", "wrong-audience", "wrong-issuer", "anon-role", "anonymous-user", "bad-subject"],
)
async def test_invalid_claims_are_rejected(signing_key, overrides):
    private_key, provider = signing_key
    with pytest.raises(AuthenticationError):
        await verify_access_token(make_token(private_key, **overrides), provider)


@pytest.mark.anyio
async def test_token_signed_by_another_key_is_rejected(signing_key):
    _, provider = signing_key
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthenticationError):
        await verify_access_token(make_token(attacker_key), provider)


@pytest.mark.anyio
async def test_symmetric_and_unsigned_tokens_are_rejected(signing_key):
    _, provider = signing_key
    hs256 = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated"}, "s" * 32, algorithm="HS256", headers={"kid": KID})
    with pytest.raises(AuthenticationError):
        await verify_access_token(hs256, provider)
    with pytest.raises(AuthenticationError):
        await verify_access_token("not.a.jwt", provider)


def test_protected_endpoints_require_a_valid_bearer_token(signing_key):
    private_key, _ = signing_key
    with TestClient(app) as client:
        assert client.get(f"/api/assessments/{uuid.uuid4()}/ai-status").status_code == 401
        response = client.get(f"/api/assessments/{uuid.uuid4()}/ai-status", headers={"Authorization": "Bearer junk"})
        assert response.status_code == 401
        assert response.json() == {
            "error": {"code": "not_authenticated", "message": "Your session has ended. Please log in again."}
        }
        expired = make_token(private_key, exp=int(time.time()) - 60)
        response = client.post(
            "/api/realtime/client-secrets", json={"purpose": "setup"}, headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401


def test_errors_never_echo_submitted_values():
    with TestClient(app) as client:
        response = client.post(
            "/api/setup-assistant/tools/set_duration",
            json={"setup": "SECRET-VALUE", "arguments": {}},
            headers={"Authorization": "Bearer junk"},
        )
    assert "SECRET-VALUE" not in response.text
