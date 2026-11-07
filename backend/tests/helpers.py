"""Helpers for API tests."""

import time

from fastapi.testclient import TestClient


def auth(token: str = "token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_for_run(client: TestClient, run_id: str, token: str = "token-a", timeout: float = 10.0, *, follow: bool = True) -> dict:
    """Polls a background job until it finishes, like the browser does.

    A generation opens the exam once its questions are written, and the other versions and the quality
    check follow in their own job; with `follow` (the default) that job is waited for too, as the exam
    page does. The generation's own result is returned."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}", headers=auth(token)).json()
        if run["status"] != "running":
            if follow and run["kind"] == "generation" and run["status"] == "succeeded":
                _wait_for_follow_up(client, run, token, deadline)
            return run
        time.sleep(0.02)
    raise AssertionError("the job didn't finish")


def _wait_for_follow_up(client: TestClient, run: dict, token: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        status = client.get(f"/api/assessments/{run['assessmentId']}/ai-status", headers=auth(token)).json()
        if not any(r["kind"] in ("versioning", "review") and r["status"] == "running" for r in status["runs"]):
            return
        time.sleep(0.02)
    raise AssertionError("the versions and quality check didn't finish")
