"""Pinned pricing: every expected number below is worked out by hand in EVIDENCE.md."""
from app.config import PRICING
from app.services.cost import breakdown, format_usd, token_cost_micros

ZERO = {"api_calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}


def test_pricing_constants_are_pinned():
    assert PRICING == {"api_call_micros": 500, "input_per_mtok_micros": 300_000,
                       "cached_input_per_mtok_micros": 30_000, "output_per_mtok_micros": 2_500_000}


def test_worked_example():
    # uncached 1,000 x 0.30 + cached 200 x 0.03 + (2,500 + 300) x 2.50, per 1M tokens
    # = 300,000,000 + 6,000,000 + 7,000,000,000 = 7,306,000,000 / 1,000,000 = 7,306 micro-USD
    assert token_cost_micros(1200, 200, 2500, 300) == 7_306


def test_cached_input_is_ten_times_cheaper():
    assert token_cost_micros(1_000_000, 0, 0, 0) == 300_000
    assert token_cost_micros(1_000_000, 1_000_000, 0, 0) == 30_000


def test_reasoning_tokens_cost_the_same_as_output():
    assert token_cost_micros(0, 0, 0, 1_000_000) == token_cost_micros(0, 0, 1_000_000, 0) == 2_500_000


def test_categories_are_not_simply_added():
    # Naive math would bill the cached tokens twice: once as input, once as cached.
    naive = (1200 * 300_000 + 200 * 30_000 + 2500 * 2_500_000) // 1_000_000
    assert naive == 6_616
    assert token_cost_micros(1200, 200, 2500, 0) == 6_556


def test_rounding_happens_once_per_period_not_per_request():
    # One output token = 2.5 micros. Rounded per request x3 = 9; rounded once on totals = 7.5 -> 8.
    per_request = 3 * token_cost_micros(0, 0, 1, 0)
    monthly = breakdown({**ZERO, "output_tokens": 3}, 0)["tokens_micros"]
    assert (per_request, monthly) == (9, 8)


def test_breakdown_adds_plan_fee_and_api_calls():
    b = breakdown({"api_calls": 2, "input_tokens": 2400, "cached_input_tokens": 400,
                   "output_tokens": 5000, "reasoning_tokens": 600}, monthly_fee_micros=29_000_000)
    assert b == {"currency": "usd", "unit": "micro_usd", "plan_fee_micros": 29_000_000, "api_calls_micros": 1_000,
                 "tokens_micros": 14_612, "total_micros": 29_015_612, "total_usd": "29.015612"}


def test_format_usd():
    assert format_usd(0) == "0.000000"
    assert format_usd(7_806) == "0.007806"


def test_usage_endpoint_matches_pinned_pricing(client, make_tenant):
    t = make_tenant("pro")
    body = {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 2500, "reasoning_tokens": 300}
    for i in range(3):
        r = client.post("/generate", json=body, headers={"X-API-Key": t["key"], "Idempotency-Key": f"p-{i}"})
        assert r.json()["request_cost_micros"] == 7_806
    cost = client.get("/usage", headers={"X-API-Key": t["key"]}).json()["cost"]
    assert cost["api_calls_micros"] == 1_500
    assert cost["tokens_micros"] == 21_918
    assert cost["total_micros"] == 29_000_000 + 1_500 + 21_918
