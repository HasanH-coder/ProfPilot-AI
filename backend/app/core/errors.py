"""Errors the API can return, and how they reach the browser.

Every error response has the same shape:

    {"error": {"code": "not_found", "message": "This assessment doesn't exist."}}

The message is always safe to show a professor. Internal details (stack traces,
database errors, OpenAI responses, prompts, file contents) are never included;
they are logged on the server without professor content where needed.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("profpilot")


class AppError(Exception):
    """An error with a status code and a message that is safe to show."""

    status_code = 400
    code = "bad_request"
    default_message = "The request couldn't be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.default_message
        if code:
            self.code = code
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = 401
    code = "not_authenticated"
    default_message = "Your session has ended. Please log in again."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "This item doesn't exist, or you don't have access to it."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_message = "This can't be done right now."


class InvalidInputError(AppError):
    status_code = 422
    code = "invalid_input"
    default_message = "Some of the information isn't valid."


class AIServiceError(AppError):
    """The AI service failed, timed out, or returned something unusable."""

    status_code = 502
    code = "ai_unavailable"
    default_message = "ProfPilot's AI service couldn't complete this. Please try again."


class AIBusyError(AIServiceError):
    status_code = 503
    code = "ai_busy"
    default_message = "ProfPilot's AI service is busy right now. Please try again in a minute."


class AITimeoutError(AIServiceError):
    status_code = 504
    code = "ai_timeout"
    default_message = "The AI took too long to respond. Please try again."


class DatabaseError(AppError):
    status_code = 502
    code = "database_error"
    default_message = "Your data couldn't be saved or loaded. Please try again."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        # Field locations only; never echo the submitted values back.
        fields = sorted({".".join(str(part) for part in item["loc"][1:]) for item in error.errors()})
        message = "Some of the information isn't valid"
        if fields:
            message += f" ({', '.join(fields[:5])})"
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_input", "message": message + "."}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # The type and route only: exception messages can contain professor content.
        logger.error("Unexpected %s on %s %s", type(error).__name__, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Something went wrong. Please try again."}},
        )
