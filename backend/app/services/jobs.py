"""Long AI jobs that run after the request returns, with progress in ai_runs.

The browser starts a job, gets its id back immediately, and polls its status.
Each job records its current stage ("planning", "generating questions", …;
never a made-up percentage) and a heartbeat. A job whose heartbeat stops (the
server restarted, the session expired) is reported as interrupted, never left
"running" forever. A partial unique index allows only one running job of each
kind per assessment.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.openai_service import Usage
from app.core.errors import AppError, AuthenticationError, ConflictError
from app.db.supabase import Database

logger = logging.getLogger("profpilot.jobs")

HEARTBEAT_SECONDS = 30
# No heartbeat for this long means the job was interrupted.
STALE_AFTER = timedelta(minutes=3)
RUN_COLUMNS = (
    "id, exam_project_id, exam_id, kind, status, stage, error_message, "
    "input_tokens, output_tokens, started_at, heartbeat_at, finished_at"
)

INTERRUPTED_MESSAGE = "This was interrupted before it finished. Please try again."


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def is_stale(run: dict[str, Any]) -> bool:
    if run.get("status") != "running":
        return False
    heartbeat = run.get("heartbeat_at") or run.get("started_at")
    try:
        when = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(UTC) - when > STALE_AFTER


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    """The run as the browser sees it; an interrupted job reads as failed."""
    status = run["status"]
    error = run.get("error_message")
    if is_stale(run):
        status, error = "failed", INTERRUPTED_MESSAGE
    return {
        "id": run["id"],
        "assessmentId": run["exam_project_id"],
        "examId": run.get("exam_id"),
        "kind": run["kind"],
        "status": status,
        "stage": run.get("stage"),
        "errorMessage": error,
        "startedAt": run.get("started_at"),
        "finishedAt": run.get("finished_at"),
    }


@dataclass
class RunContext:
    """What a job uses to report progress."""

    run_id: str
    db: Database
    usage: Usage = field(default_factory=Usage)

    async def stage(self, name: str) -> None:
        await self._update({"stage": name, "heartbeat_at": now_iso()})

    async def heartbeat(self) -> None:
        await self._update(
            {
                "heartbeat_at": now_iso(),
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            }
        )

    async def _update(self, values: dict[str, Any]) -> None:
        await self.db.update("ai_runs", values, filters=[("id", "eq", self.run_id)], columns="id")


Work = Callable[[RunContext], Awaitable[None]]


class JobRunner:
    def __init__(self) -> None:
        # Keeps references so running tasks aren't garbage-collected.
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        db: Database,
        *,
        assessment_id: str,
        kind: str,
        work: Work,
        exam_id: str | None = None,
        stage: str = "starting",
    ) -> dict[str, Any]:
        await self._expire_stale(db, assessment_id, kind)
        try:
            rows = await db.insert(
                "ai_runs",
                {"exam_project_id": assessment_id, "exam_id": exam_id, "kind": kind, "stage": stage},
                columns=RUN_COLUMNS,
            )
        except ConflictError as error:
            raise ConflictError("This is already in progress for this assessment.", code="job_running") from error
        run = rows[0]
        context = RunContext(run_id=run["id"], db=db)
        task = asyncio.create_task(self._run(context, work, kind))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def wait_all(self) -> None:
        """Waits for every running job (used by tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _expire_stale(self, db: Database, assessment_id: str, kind: str) -> None:
        running = await db.select(
            "ai_runs",
            columns=RUN_COLUMNS,
            filters=[("exam_project_id", "eq", assessment_id), ("kind", "eq", kind), ("status", "eq", "running")],
        )
        for run in running:
            if is_stale(run):
                await db.update(
                    "ai_runs",
                    {"status": "failed", "error_message": INTERRUPTED_MESSAGE, "finished_at": now_iso()},
                    filters=[("id", "eq", run["id"]), ("status", "eq", "running")],
                    columns="id",
                )

    async def _run(self, context: RunContext, work: Work, kind: str) -> None:
        beat = asyncio.create_task(self._beat(context))
        try:
            await work(context)
        except AuthenticationError:
            # The professor's session expired mid-job; nothing can be written any
            # more. The missing heartbeat reports it as interrupted.
            logger.warning("%s job %s: session expired", kind, context.run_id)
            return
        except AppError as error:
            await self._finish(context, "failed", error.message)
            return
        except Exception as error:  # never leak internals; log the type only
            logger.error("%s job %s failed: %s", kind, context.run_id, type(error).__name__)
            await self._finish(context, "failed", "Something went wrong. Please try again.")
            return
        finally:
            beat.cancel()
        await self._finish(context, "succeeded", None)

    async def _beat(self, context: RunContext) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await context.heartbeat()
            except Exception:
                return

    async def _finish(self, context: RunContext, status: str, message: str | None) -> None:
        try:
            await context.db.update(
                "ai_runs",
                {
                    "status": status,
                    "stage": "done" if status == "succeeded" else "failed",
                    "error_message": message,
                    "finished_at": now_iso(),
                    "heartbeat_at": now_iso(),
                    "input_tokens": context.usage.input_tokens,
                    "output_tokens": context.usage.output_tokens,
                },
                filters=[("id", "eq", context.run_id)],
                columns="id",
            )
        except Exception as error:
            logger.warning("Could not record the end of job %s: %s", context.run_id, type(error).__name__)


job_runner = JobRunner()
