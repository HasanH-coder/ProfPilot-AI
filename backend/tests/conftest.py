"""Shared fixtures. OpenAI and Supabase are always faked: tests make no network calls."""

from collections.abc import Iterator

import pytest
from fastapi import Header
from fastapi.testclient import TestClient

from app.api.deps import get_ai, get_db
from app.core.errors import AuthenticationError
from app.main import app
from app.services import realtime_session
from tests.fakes import FakeAI, FakeDatabase, FakeSupabase
from tests.model import install_well_behaved
from tests.seed import PROF_A, PROF_B, TOKENS


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def supabase() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def ai() -> FakeAI:
    return install_well_behaved(FakeAI())


@pytest.fixture
def db_a(supabase: FakeSupabase) -> FakeDatabase:
    return supabase.connect(PROF_A)


@pytest.fixture
def db_b(supabase: FakeSupabase) -> FakeDatabase:
    return supabase.connect(PROF_B)


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    realtime_session._recent.clear()


@pytest.fixture
def api(supabase: FakeSupabase, ai: FakeAI) -> Iterator[TestClient]:
    """The real API routes, with a fake database (per professor) and a fake AI behind them."""

    def fake_db(authorization: str | None = Header(default=None)) -> FakeDatabase:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token not in TOKENS:
            raise AuthenticationError()
        return supabase.connect(TOKENS[token])

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_ai] = lambda: ai
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
