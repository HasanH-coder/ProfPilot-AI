"""Helpers for API tests."""

import time

from fastapi.testclient import TestClient


def auth(token: str = "token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_for_run(client: TestClient, run_id: str, token: str = "token-a", timeout: float = 10.0) -> dict:
    """Polls a background job until it finishes, like the browser does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}", headers=auth(token)).json()
        if run["status"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError("the job didn't finish")
