from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api import metering
from app.db import close_pool, init_pool
from app.errors import AppError, app_error_handler, validation_error_handler


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_pool()
    yield
    close_pool()


app = FastAPI(title="Usage Metering & Billing Engine", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(metering.router)


@app.get("/health", summary="Liveness check")
def health():
    return {"status": "ok"}
