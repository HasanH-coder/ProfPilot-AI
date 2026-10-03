from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_returns_api_info() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"name": "ProfPilot AI API", "status": "running"}


def test_health_check_returns_healthy() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_cors_allows_local_frontend() -> None:
    # Browsers send this "preflight" request before cross-origin calls.
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
