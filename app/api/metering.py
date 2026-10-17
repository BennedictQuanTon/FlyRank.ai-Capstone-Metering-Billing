from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.api.deps import current_tenant
from app.db import transaction
from app.errors import AppError
from app.repo import usage as usage_repo
from app.services import meter, quota
from app.services.periods import next_period_start, period_start

router = APIRouter()

MAX_TOKENS = 10_000_000


class GenerateRequest(BaseModel):
    """Simulated AI usage for one request. No model is called; we meter the numbers."""
    input_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Total prompt tokens, including cached ones")
    cached_input_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Subset of input_tokens served from cache")
    output_tokens: int = Field(0, ge=0, le=MAX_TOKENS)
    reasoning_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Thinking tokens, billed as output")

    @model_validator(mode="after")
    def cached_is_subset_of_input(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens (cached tokens are part of the input)")
        return self


@router.post("/generate", status_code=201, summary="The billable action: meter one AI request")
def generate(body: GenerateRequest, tenant: dict = Depends(current_tenant),
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not idempotency_key or not (1 <= len(idempotency_key) <= 255):
        raise AppError(400, "missing_idempotency_key",
                       "Send a unique Idempotency-Key header (1-255 chars). Reuse it only when retrying the same request.")
    status, payload, replayed = meter.record(tenant["id"], idempotency_key, body.model_dump())
    return JSONResponse(payload, status_code=status, headers={"Idempotent-Replayed": "true" if replayed else "false"})


@router.get("/usage", summary="This month's usage, limits and cost for the calling tenant")
def get_usage(tenant: dict = Depends(current_tenant)):
    period = period_start()
    with transaction() as conn:
        totals = usage_repo.period_totals(conn, tenant["id"], period)
    used_tokens = quota.billable_tokens(totals)
    return {
        "tenant": {"id": str(tenant["id"]), "name": tenant["name"]},
        "plan": tenant["plan_code"],
        "status": tenant["status"],
        "period": {"start": period.isoformat(), "resets_at": next_period_start().isoformat()},
        "used": {"api_calls": totals["api_calls"], "tokens": used_tokens, "events": totals["events"]},
        "limits": {"api_calls": tenant["api_call_limit"], "tokens": tenant["token_limit"]},
        "remaining": {"api_calls": max(0, tenant["api_call_limit"] - totals["api_calls"]),
                      "tokens": max(0, tenant["token_limit"] - used_tokens)},
    }
