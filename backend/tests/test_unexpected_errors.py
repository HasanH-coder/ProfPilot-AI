"""A crash reaches the browser as a readable error, not as a reply CORS hides."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import install_error_handlers


def crashing_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/crash")
    async def crash():
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def test_an_allowed_origin_can_read_the_error():
    origin = settings.cors_origin_list[0]
    response = crashing_client().get("/crash", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["Access-Control-Allow-Origin"] == origin


def test_any_other_origin_is_not_allowed_to():
    response = crashing_client().get("/crash", headers={"Origin": "https://elsewhere.example"})
    assert response.status_code == 500
    assert "Access-Control-Allow-Origin" not in response.headers
