"""MeterService: record one billable action exactly once per idempotency key."""
import hashlib
import json

from app.config import PRICING
from app.db import transaction
from app.errors import AppError
from app.repo import tenants as tenants_repo
from app.repo import usage as usage_repo
from app.services import cost, quota
from app.services.periods import period_start


def request_hash(usage: dict) -> str:
    return hashlib.sha256(json.dumps(usage, sort_keys=True).encode()).hexdigest()


def record(tenant_id, idempotency_key: str, usage: dict) -> tuple[int, str, bool]:
    """Returns (status, serialized JSON body, replayed). The whole check-and-insert is one transaction."""
    req_hash = request_hash(usage)
    period = period_start()
    with transaction() as conn:
        # 1. Lock the tenant: concurrent requests for this tenant now run one at a time.
        tenant = tenants_repo.get_for_update(conn, tenant_id)

        # 2. Same key seen before? Replay the original response, write nothing new.
        prior = usage_repo.get_idempotent_response(conn, tenant_id, idempotency_key)
        if prior:
            if prior["request_hash"] != req_hash:
                raise AppError(422, "idempotency_key_reused",
                               "This Idempotency-Key was already used with a different request body. "
                               "Use a new key for a new request.")
            return prior["response_status"], prior["response_body"], True

        # 3. Quota check against this period's rollup.
        totals = usage_repo.period_totals(conn, tenant_id, period)
        used = {"api_calls": totals["api_calls"], "tokens": quota.billable_tokens(totals)}
        requested_tokens = quota.billable_tokens(usage)
        quota.check(tenant, used, requested_calls=1, requested_tokens=requested_tokens)

        # 4. Record exactly one usage event and remember the response for retries.
        event_id = usage_repo.insert_event(conn, tenant_id, idempotency_key, period, usage)
        body = {
            "usage_event_id": event_id,
            "tenant_id": str(tenant_id),
            "period_start": period.isoformat(),
            "recorded": {"api_calls": 1, **usage, "billable_tokens": requested_tokens},
            # Informational per-request price; the monthly bill is computed from period totals (see GET /usage).
            "request_cost_micros": PRICING["api_call_micros"] + cost.token_cost_micros(**usage),
            "usage_after": {"api_calls": used["api_calls"] + 1, "tokens": used["tokens"] + requested_tokens},
            "limits": {"api_calls": tenant["api_call_limit"], "tokens": tenant["token_limit"]},
        }
        serialized = json.dumps(body)
        usage_repo.store_idempotent_response(conn, tenant_id, idempotency_key, req_hash, 201, serialized)
        return 201, serialized, False
