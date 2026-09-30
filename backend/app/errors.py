"""Reusable error handling: users get safe messages, logs get the details."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("dreamcast")

GENERIC = "Something went wrong. Please try again."


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request"):
        self.message, self.status_code, self.code = message, status_code, code


class NotFound(AppError):
    def __init__(self, message: str = "Not found."):
        super().__init__(message, 404, "not_found")


class Forbidden(AppError):
    def __init__(self, message: str = "You do not have access to this."):
        super().__init__(message, 403, "forbidden")


class Unauthorized(AppError):
    def __init__(self, message: str = "Please sign in to continue."):
        super().__init__(message, 401, "unauthorized")


def _body(message: str, code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return _body(exc.message, exc.code, exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else GENERIC
        return _body(msg, "http_error", exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
        msg = f"Invalid value for '{field}'." if field else "Invalid request."
        return _body(msg, "validation_error", 422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _body(GENERIC, "internal_error", 500)
