"""Quota rule: allowed when used + requested <= limit (the request that lands exactly on the limit succeeds).

Blocked requests:
  - tenant past_due                 -> 402 (fix payment first)
  - Free plan would exceed a limit  -> 402 (upgrade to Pro unlocks it)
  - Pro plan would exceed a limit   -> 429 + Retry-After (wait for the next period)
"""
from app.errors import AppError
from app.services.periods import next_period_start, seconds_until_reset


def billable_tokens(usage: dict) -> int:
    # cached_input_tokens is a subset of input_tokens, so it is not added again.
    return usage["input_tokens"] + usage["output_tokens"] + usage["reasoning_tokens"]


def check(tenant: dict, used: dict, requested_calls: int, requested_tokens: int) -> None:
    if tenant["status"] == "past_due":
        raise AppError(402, "payment_required",
                       "Your last payment failed. Update your payment method in Stripe to resume billable requests.")

    over = []
    if used["api_calls"] + requested_calls > tenant["api_call_limit"]:
        over.append(("api_calls", used["api_calls"], tenant["api_call_limit"], requested_calls))
    if used["tokens"] + requested_tokens > tenant["token_limit"]:
        over.append(("ai_tokens", used["tokens"], tenant["token_limit"], requested_tokens))
    if not over:
        return

    metric, used_n, limit, requested = over[0]
    details = {"metric": metric, "used": used_n, "limit": limit, "requested": requested, "plan": tenant["plan_code"]}
    if tenant["plan_code"] == "free":
        raise AppError(402, "upgrade_required",
                       f"Free plan {metric} quota reached ({used_n}/{limit} used, this request needs {requested}). "
                       "Upgrade to Pro via POST /billing/checkout to continue this month.", **details)
    raise AppError(429, "quota_exceeded",
                   f"{tenant['plan_name']} plan {metric} quota reached ({used_n}/{limit} used, this request needs {requested}). "
                   f"The quota resets at {next_period_start().isoformat()}.",
                   headers={"Retry-After": str(seconds_until_reset())}, **details)
