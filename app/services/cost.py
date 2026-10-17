"""Turn usage into money. Integers only: tokens x (micro-USD per 1M tokens) is exact, then one rounding step."""
from app.config import PRICING

MTOK = 1_000_000


def round_half_up_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def token_cost_micros(input_tokens: int, cached_input_tokens: int, output_tokens: int, reasoning_tokens: int,
                      pricing: dict = PRICING) -> int:
    # input_tokens already contains the cached ones, so split instead of adding categories together.
    uncached_input = input_tokens - cached_input_tokens
    numerator = (uncached_input * pricing["input_per_mtok_micros"]
                 + cached_input_tokens * pricing["cached_input_per_mtok_micros"]
                 + (output_tokens + reasoning_tokens) * pricing["output_per_mtok_micros"])
    return round_half_up_div(numerator, MTOK)


def breakdown(totals: dict, monthly_fee_micros: int, pricing: dict = PRICING) -> dict:
    """Cost for a whole period, computed from the period's totals so rounding happens once, not per request."""
    api = totals["api_calls"] * pricing["api_call_micros"]
    tokens = token_cost_micros(totals["input_tokens"], totals["cached_input_tokens"],
                               totals["output_tokens"], totals["reasoning_tokens"], pricing)
    total = monthly_fee_micros + api + tokens
    return {
        "currency": "usd",
        "unit": "micro_usd",
        "plan_fee_micros": monthly_fee_micros,
        "api_calls_micros": api,
        "tokens_micros": tokens,
        "total_micros": total,
        "total_usd": format_usd(total),
    }


def format_usd(micros: int) -> str:
    return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"
