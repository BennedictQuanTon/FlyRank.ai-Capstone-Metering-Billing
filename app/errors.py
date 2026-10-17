from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An expected, explainable failure that maps to a clean 4xx response."""

    def __init__(self, status: int, code: str, message: str, headers: dict | None = None, **details):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        self.details = details


def error_body(code: str, message: str, **details) -> dict:
    return {"error": {"code": code, "message": message, **details}}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, **exc.details), status_code=exc.status, headers=exc.headers)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    problems = [{"field": ".".join(str(p) for p in e["loc"][1:]), "problem": e["msg"]} for e in exc.errors()]
    return JSONResponse(error_body("invalid_request", "Request body failed validation.", problems=problems), status_code=422)
