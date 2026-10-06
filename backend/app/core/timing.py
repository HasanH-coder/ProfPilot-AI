"""Timing logs for AI operations: what ran and how long it took, never any content.

Each line names the operation, its elapsed time, and how many reasoning-model
calls, embedding requests and course-material searches it made, with the
purpose of each model call, for example:

    generate_next_question: 8412 ms, 1 model call(s) [generate_question], 1 embedding call(s), 1 retrieval(s)

No prompts, files, answers or ids are logged.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.openai_service import Usage

logger = logging.getLogger("profpilot.timing")


@asynccontextmanager
async def timed(operation: str, usage: "Usage") -> AsyncIterator[None]:
    """Logs the operation's duration and the calls `usage` recorded while it ran."""
    started = time.monotonic()
    model_calls, embedding_calls, retrievals = usage.model_calls, usage.embedding_calls, usage.retrievals
    purposes = len(usage.purposes)
    outcome = "failed"
    try:
        yield
        outcome = ""
    finally:
        logger.info(
            "%s: %.0f ms%s, %d model call(s) [%s], %d embedding call(s), %d retrieval(s)",
            operation,
            (time.monotonic() - started) * 1000,
            f" ({outcome})" if outcome else "",
            usage.model_calls - model_calls,
            ", ".join(usage.purposes[purposes:]),
            usage.embedding_calls - embedding_calls,
            usage.retrievals - retrievals,
        )
