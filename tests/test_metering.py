from concurrent.futures import ThreadPoolExecutor

from tests.conftest import count_events, preload_usage

BODY = {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 2500, "reasoning_tokens": 300}


def gen(client, key, idem, body=BODY):
    headers = {"X-API-Key": key}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return client.post("/generate", json=body, headers=headers)


def test_same_request_twice_creates_one_event(client, make_tenant):
    t = make_tenant()
    first = gen(client, t["key"], "req-1")
    second = gen(client, t["key"], "req-1")
    assert first.status_code == second.status_code == 201
    assert first.content == second.content  # byte-identical replay, not just equal JSON
    assert first.headers["Idempotent-Replayed"] == "false"
    assert second.headers["Idempotent-Replayed"] == "true"
    assert count_events(t["id"]) == 1


def test_concurrent_retries_with_one_key_create_one_event(client, make_tenant):
    t = make_tenant()
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: gen(client, t["key"], "same-key"), range(10)))
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["usage_event_id"] for r in responses}) == 1
    assert count_events(t["id"]) == 1


def test_key_reused_with_different_body_is_rejected(client, make_tenant):
    t = make_tenant()
    assert gen(client, t["key"], "k").status_code == 201
    r = gen(client, t["key"], "k", {**BODY, "output_tokens": 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"
    assert count_events(t["id"]) == 1


def test_same_key_in_two_tenants_is_two_events(client, make_tenant):
    a, b = make_tenant(), make_tenant()
    assert gen(client, a["key"], "shared").status_code == 201
    assert gen(client, b["key"], "shared").status_code == 201
    assert count_events(a["id"]) == count_events(b["id"]) == 1


def test_free_boundary_exact_limit_allowed_then_402(client, make_tenant):
    t = make_tenant("free")
    preload_usage(t["id"], 999)
    at_limit = gen(client, t["key"], "call-1000", {})
    assert at_limit.status_code == 201
    assert at_limit.json()["usage_after"]["api_calls"] == 1000
    over = gen(client, t["key"], "call-1001", {})
    assert over.status_code == 402
    err = over.json()["error"]
    assert err["code"] == "upgrade_required"
    assert (err["used"], err["limit"], err["requested"]) == (1000, 1000, 1)
    assert count_events(t["id"]) == 1000


def test_pro_over_limit_is_429_with_retry_after(client, make_tenant):
    t = make_tenant("pro")
    preload_usage(t["id"], 50_000)
    r = gen(client, t["key"], "late", {})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exceeded"
    assert int(r.headers["Retry-After"]) > 0


def test_token_quota_counts_input_output_reasoning_not_cached_twice(client, make_tenant):
    t = make_tenant("free")
    preload_usage(t["id"], 1, tokens_per_call=95_000)
    # billable = 3000 + 1500 + 500 = 5000 -> lands exactly on 100,000
    exact = gen(client, t["key"], "exact", {"input_tokens": 3000, "cached_input_tokens": 3000,
                                             "output_tokens": 1500, "reasoning_tokens": 500})
    assert exact.status_code == 201
    assert exact.json()["usage_after"]["tokens"] == 100_000
    over = gen(client, t["key"], "over", {"input_tokens": 1})
    assert over.status_code == 402
    assert over.json()["error"]["metric"] == "ai_tokens"


def test_concurrent_requests_cannot_overshoot_quota(client, make_tenant):
    t = make_tenant("free")
    preload_usage(t["id"], 995)
    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = list(pool.map(lambda i: gen(client, t["key"], f"c-{i}", {}).status_code, range(20)))
    assert codes.count(201) == 5
    assert codes.count(402) == 15
    assert count_events(t["id"]) == 1000


def test_rejected_request_is_not_stored_and_can_succeed_later(client, make_tenant):
    t = make_tenant("free")
    preload_usage(t["id"], 1000)
    assert gen(client, t["key"], "retry-me", {}).status_code == 402
    from app.db import transaction
    with transaction() as conn:
        conn.execute("UPDATE tenants SET plan_code = 'pro' WHERE id = %s", (t["id"],))
    assert gen(client, t["key"], "retry-me", {}).status_code == 201


def test_past_due_tenant_gets_402(client, make_tenant):
    t = make_tenant("pro")
    from app.db import transaction
    with transaction() as conn:
        conn.execute("UPDATE tenants SET status = 'past_due' WHERE id = %s", (t["id"],))
    r = gen(client, t["key"], "x", {})
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "payment_required"


def test_validation_is_clean_4xx(client, make_tenant):
    t = make_tenant()
    assert gen(client, t["key"], None).status_code == 400
    assert gen(client, t["key"], "a", {"input_tokens": -1}).status_code == 422
    bad = gen(client, t["key"], "b", {"input_tokens": 10, "cached_input_tokens": 11})
    assert bad.status_code == 422
    assert client.post("/generate", json={}, headers={"Idempotency-Key": "c"}).status_code == 401
    assert client.post("/generate", json={}, headers={"Idempotency-Key": "c", "X-API-Key": "nope"}).status_code == 401
    assert count_events(t["id"]) == 0


def test_usage_rollup_is_isolated_per_tenant(client, make_tenant):
    a, b = make_tenant(), make_tenant()
    gen(client, a["key"], "1")
    gen(client, a["key"], "2")
    ua = client.get("/usage", headers={"X-API-Key": a["key"]}).json()
    ub = client.get("/usage", headers={"X-API-Key": b["key"]}).json()
    assert ua["used"]["api_calls"] == 2
    assert ua["used"]["tokens"] == 2 * (1200 + 2500 + 300)
    assert ub["used"]["api_calls"] == 0
