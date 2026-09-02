"""Application-level exceptions and their FastAPI handlers.

Business/service code raises these instead of HTTPException so error
semantics stay decoupled from the transport layer. New exception types
get added here as later components (subscriptions, delivery, etc.) need
them.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from webhook_service.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all domain errors. Carries an HTTP status + error code."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class TenantRequiredError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "tenant_required"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_code, "message": exc.message},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": "An unexpected error occurred."},
        )
