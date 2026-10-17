# EVIDENCE

One pasted proof for every requirement in Section 6 of the brief. Every transcript below is real output from a clean run:

```bash
docker compose down -v && docker compose up --build -d
docker compose exec -T api python -m scripts.seed
docker compose exec -T api pytest -v
docker compose exec -T api python -m scripts.probes
```

Status: ✅ proven. Stripe parts ran against a real Stripe **test-mode** account with `stripe listen --forward-to localhost:8000/webhooks/stripe` (Stripe CLI 1.54.0).

## Test suite (29 tests, real Postgres)

```text
tests/test_metering.py::test_same_request_twice_creates_one_event PASSED
tests/test_metering.py::test_concurrent_retries_with_one_key_create_one_event PASSED
tests/test_metering.py::test_key_reused_with_different_body_is_rejected PASSED
tests/test_metering.py::test_same_key_in_two_tenants_is_two_events PASSED
tests/test_metering.py::test_free_boundary_exact_limit_allowed_then_402 PASSED
tests/test_metering.py::test_pro_over_limit_is_429_with_retry_after PASSED
tests/test_metering.py::test_token_quota_counts_input_output_reasoning_not_cached_twice PASSED
tests/test_metering.py::test_concurrent_requests_cannot_overshoot_quota PASSED
tests/test_metering.py::test_rejected_request_is_not_stored_and_can_succeed_later PASSED
tests/test_metering.py::test_past_due_tenant_gets_402 PASSED
tests/test_metering.py::test_validation_is_clean_4xx PASSED
tests/test_metering.py::test_usage_rollup_is_isolated_per_tenant PASSED
tests/test_pricing.py::test_pricing_constants_are_pinned PASSED
tests/test_pricing.py::test_worked_example PASSED
tests/test_pricing.py::test_cached_input_is_ten_times_cheaper PASSED
tests/test_pricing.py::test_reasoning_tokens_cost_the_same_as_output PASSED
tests/test_pricing.py::test_categories_are_not_simply_added PASSED
tests/test_pricing.py::test_rounding_happens_once_per_period_not_per_request PASSED
tests/test_pricing.py::test_breakdown_adds_plan_fee_and_api_calls PASSED
tests/test_pricing.py::test_format_usd PASSED
tests/test_pricing.py::test_usage_endpoint_matches_pinned_pricing PASSED
tests/test_webhooks.py::test_forged_signature_is_400_and_changes_nothing PASSED
tests/test_webhooks.py::test_tampered_body_fails_verification PASSED
tests/test_webhooks.py::test_checkout_flips_free_to_pro_and_usage_shows_new_limits PASSED
tests/test_webhooks.py::test_replayed_event_is_processed_once PASSED
tests/test_webhooks.py::test_subscription_lifecycle_updates_plan PASSED
tests/test_webhooks.py::test_out_of_order_event_cannot_undo_newer_state PASSED
tests/test_webhooks.py::test_unrelated_events_are_acknowledged_and_ignored PASSED
tests/test_webhooks.py::test_failing_event_retries_then_alerts PASSED
============================== 29 passed in 1.10s ==============================
```

---

## Metering

### ✅ A billable action creates exactly one usage event, even under retries

Live **Probe 1**: the same `POST /generate` sent twice with one `Idempotency-Key`.

**first** → `POST /generate` → **201** {'Idempotent-Replayed': 'false'}
```json
{
  "usage_event_id": 1,
  "recorded": {
    "api_calls": 1,
    "input_tokens": 1200,
    "cached_input_tokens": 200,
    "output_tokens": 2500,
    "reasoning_tokens": 300,
    "billable_tokens": 4000
  },
  "request_cost_micros": 7806,
  "usage_after": {
    "api_calls": 1,
    "tokens": 4000
  }
}
```
**retry (same key)** → `POST /generate` → **201** {'Idempotent-Replayed': 'true'}
```json
{
  "usage_event_id": 1,
  "recorded": {
    "api_calls": 1,
    "input_tokens": 1200,
    "cached_input_tokens": 200,
    "output_tokens": 2500,
    "reasoning_tokens": 300,
    "billable_tokens": 4000
  },
  "request_cost_micros": 7806,
  "usage_after": {
    "api_calls": 1,
    "tokens": 4000
  }
}
```
usage events before: 0, after: 1, created: **1**. Bodies byte-identical: **True**

Under concurrency: `test_concurrent_retries_with_one_key_create_one_event` fires the same key from 10 threads at once. All 10 get `201` with the same `usage_event_id`, and the database holds exactly 1 row. The tenant row lock (`SELECT … FOR UPDATE` in `app/services/meter.py`) serializes them.

### ✅ Proof that double-counting cannot happen

Three layers, each proven:

1. **Application:** the stored response is replayed and no insert runs (Probe 1 above: `created: 1`, `Idempotent-Replayed: true`).
2. **Misuse:** the same key with a different body returns `422 idempotency_key_reused` and writes nothing (`test_key_reused_with_different_body_is_rejected`).
3. **Database:** even if the code were wrong, the schema rejects a duplicate:

```text
-- insert a second event for Probe 1's idempotency key, bypassing the app:
ERROR:  duplicate key value violates unique constraint "usage_events_tenant_id_idempotency_key_key"
DETAIL:  Key (tenant_id, idempotency_key)=(08eac36a-c537-4a57-9394-7c17920f4238, probe1-047c26eb) already exists.
             name             | plan_code | usage_events 
------------------------------+-----------+--------------
 Acme (Free)                  | free      |         1000
 Globex (Pro)                 | pro       |            1
 Initech (Free, for checkout) | pro       |            0
(3 rows)
```

---

## Quotas

### ✅ Usage is checked against the tenant's plan; requests over the limit are rejected

Live **Probe 2**: the Free tenant (1,000 calls/month) is driven over real HTTP.

Sent 999 requests to reach 999/1000 API calls.

**request #1000 (lands exactly on the limit)** → `POST /generate` → **201** {'Idempotent-Replayed': 'false'}
```json
{
  "usage_after": {
    "api_calls": 1000,
    "tokens": 0
  },
  "limits": {
    "api_calls": 1000,
    "tokens": 100000
  }
}
```
**request #1001** → `POST /generate` → **402** 
```json
{
  "error": {
    "code": "upgrade_required",
    "message": "Free plan api_calls quota reached (1000/1000 used, this request needs 1). Upgrade to Pro via POST /billing/checkout to continue this month.",
    "metric": "api_calls",
    "used": 1000,
    "limit": 1000,
    "requested": 1,
    "plan": "free"
  }
}
```
**GET /usage** → `GET /usage` → **200** 
```json
{
  "plan": "free",
  "used": {
    "api_calls": 1000,
    "tokens": 0,
    "events": 1000
  },
  "limits": {
    "api_calls": 1000,
    "tokens": 100000
  },
  "remaining": {
    "api_calls": 0,
    "tokens": 100000
  }
}
```

Boundary rule: allowed when `used + requested <= limit`. Call 1,000 succeeds and call 1,001 is rejected. Under concurrency, `test_concurrent_requests_cannot_overshoot_quota` starts at 995 used and fires 20 requests at once: exactly **5 × 201 and 15 × 402**, and the final count is exactly 1,000.

### ✅ Responses carry the correct status codes (429 / 402) and a message explaining why

| Case | Status | Proof |
|---|---|---|
| Free plan over quota | `402 upgrade_required` | Probe 2 above, `test_free_boundary_exact_limit_allowed_then_402` |
| Pro plan over quota | `429 quota_exceeded` + `Retry-After` (seconds to the next period) | `test_pro_over_limit_is_429_with_retry_after` |
| Payment failed (`past_due`) | `402 payment_required` | `test_past_due_tenant_gets_402` |
| Token quota rather than calls | `402`, `"metric": "ai_tokens"` | `test_token_quota_counts_input_output_reasoning_not_cached_twice` |

Every block includes `metric`, `used`, `limit`, `requested`, `plan` and a human-readable `message`.

---

## Cost calculation

### ✅ Monthly usage rolls up into a cost figure per tenant

Live **Probe 5**: `GET /usage` for the Pro tenant after the single Probe 1 request.

**GET /usage (Pro tenant)** → `GET /usage` → **200** 
```json
{
  "plan": "pro",
  "used": {
    "api_calls": 1,
    "tokens": 4000,
    "events": 1
  },
  "tokens_detail": {
    "input_tokens": 1200,
    "cached_input_tokens": 200,
    "output_tokens": 2500,
    "reasoning_tokens": 300
  },
  "cost": {
    "currency": "usd",
    "unit": "micro_usd",
    "plan_fee_micros": 29000000,
    "api_calls_micros": 500,
    "tokens_micros": 7306,
    "total_micros": 29007806,
    "total_usd": "29.007806"
  },
  "pricing": {
    "api_call_micros": 500,
    "input_per_mtok_micros": 300000,
    "cached_input_per_mtok_micros": 30000,
    "output_per_mtok_micros": 2500000
  }
}
```

### ✅ AI token pricing handles cached input, reasoning and output correctly

The worked example is the exact request used in Probe 1, `input 1,200 (of which cached 200) · output 2,500 · reasoning 300`:

| Line | Tokens | Rate (micro-USD / 1M) | Amount (micro-USD × 10⁻⁶) |
|---|---:|---:|---:|
| Uncached input = 1,200 − 200 | 1,000 | 300,000 | 300,000,000 |
| Cached input | 200 | 30,000 | 6,000,000 |
| Output + reasoning = 2,500 + 300 | 2,800 | 2,500,000 | 7,000,000,000 |
| **Tokens** | | | 7,306,000,000 → **7,306 micro-USD** |
| API call | 1 | 500 per call | **500 micro-USD** |
| **Request total** | | | **7,806 micro-USD = $0.007806** |

That matches `request_cost_micros: 7806` in Probe 1 and `tokens_micros: 7306` / `api_calls_micros: 500` in Probe 5.

What the rules prevent, each pinned by a test:
- **Cached tokens billed twice:** naive "add every category" gives 6,616; the correct value is 6,556 (`test_categories_are_not_simply_added`).
- **Reasoning tokens treated as free or as input:** 1M reasoning costs exactly the same as 1M output, 2,500,000 (`test_reasoning_tokens_cost_the_same_as_output`).
- **Cached input not discounted:** 1M cached = 30,000 vs 1M fresh = 300,000 (`test_cached_input_is_ten_times_cheaper`).
- **Rounding drift:** three requests of 1 output token (2.5 micros each) give 9 if rounded per request and **8** if rounded once on the month's totals, which is what we do (`test_rounding_happens_once_per_period_not_per_request`).

### ✅ Pricing constants are pinned in config, with proof of correct totals

`app/config.py` → `PRICING`. `test_pricing_constants_are_pinned` fails if anyone edits a rate without updating the evidence. `test_usage_endpoint_matches_pinned_pricing` sends 3 requests through HTTP and asserts `GET /usage` returns `tokens_micros = 21,918` (= 3 × 7,306) and `total = 29,000,000 + 1,500 + 21,918`. All money is stored and computed as integer micro-USD; no floats exist in the money path.

---

## Stripe integration

### ✅ Subscription checkout works end-to-end in Stripe test mode (Probe 3)

Implemented: `POST /billing/checkout` (`app/services/billing.py`) and `scripts/stripe_setup.py`. Unit-proven: `test_checkout_flips_free_to_pro_and_usage_shows_new_limits`, in which a signed `checkout.session.completed` is queued, the worker applies it, the tenant becomes `pro/active`, and `GET /usage` shows limits of 50,000 / 5,000,000.

Live run for tenant **Initech** (`demo_free_initech`):

**1. Before:** `GET /usage`
```json
{"plan": "free", "status": "active", "limits": {"api_calls": 1000, "tokens": 100000}}
```

**2. Start Checkout:** `POST /billing/checkout` → `200`
```json
{"checkout_url": "https://checkout.stripe.com/c/pay/cs_test_a1v4eDvT2IcYQgqlmEJDeC4gaHkpNDtHZs8dTRC7y1p5W9cjd8o4eOovgo#…", "session_id": "cs_test_a1v4eDvT2IcYQgqlmEJDeC4gaHkpNDtHZs8dTRC7y1p5W9cjd8o4eOovgo"}
```

**3. Paid** on the Stripe-hosted page with test card `4242 4242 4242 4242` → redirected to `/billing/success`.

**4. Stripe delivered signed webhooks** (`stripe listen` log):
```text
2026-10-17 22:07:47   --> checkout.session.completed [evt_1URZD4IKhLvwumC6hJSGrxdq]
2026-10-17 22:07:47  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1URZD4IKhLvwumC6hJSGrxdq]
2026-10-17 22:07:47   --> customer.subscription.created [evt_1URZD5IKhLvwumC66Au1IS35]
2026-10-17 22:07:47  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1URZD5IKhLvwumC66Au1IS35]
2026-10-17 22:07:47   --> customer.subscription.updated [evt_1URZD5IKhLvwumC6Gp7hqqTA]
2026-10-17 22:07:47  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1URZD5IKhLvwumC6Gp7hqqTA]
2026-10-17 22:08:16   --> checkout.session.completed [evt_1URZD4IKhLvwumC6hJSGrxdq]
2026-10-17 22:08:16  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1URZD4IKhLvwumC6hJSGrxdq]
```

**5. The background worker applied them** (`docker compose logs worker`):
```text
2026-10-17 15:07:47,668 INFO worker processed evt_1URZD4IKhLvwumC6hJSGrxdq checkout.session.completed: tenant 6034fd33-45c0-4b70-b895-78f277361bed -> pro (checkout completed)
2026-10-17 15:07:47,673 INFO worker processed evt_1URZD5IKhLvwumC66Au1IS35 customer.subscription.created: ignored: customer.subscription.created is not handled
2026-10-17 15:07:47,676 INFO worker processed evt_1URZD5IKhLvwumC6Gp7hqqTA customer.subscription.updated: tenant 6034fd33-45c0-4b70-b895-78f277361bed -> pro/active (subscription active)
```

**6. After:** `GET /usage` now shows Pro limits:
```json
{"plan": "pro", "status": "active", "limits": {"api_calls": 50000, "tokens": 5000000}}
```
```text
name             | plan_code | status |    stripe_subscription_id    | sub_status 
------------------------------+-----------+--------+------------------------------+------------
 Initech (Free, for checkout) | pro       | active | sub_1URZD0IKhLvwumC6i5sNZ9W5 | active
(1 row)
```

### ✅ Webhooks verify signatures, ignore duplicate events, and update tenant plan/status (Probe 4)

Unit-proven with real HMAC-SHA256 Stripe signatures:
- Forged signature → `400 invalid_signature`, nothing stored, plan unchanged (`test_forged_signature_is_400_and_changes_nothing`)
- Body tampered after signing → `400` (`test_tampered_body_fails_verification`)
- Same event posted twice → second is `duplicate_ignored`; processed exactly once, `attempts = 1` (`test_replayed_event_is_processed_once`)
- `past_due` → tenant `pro/past_due` → `402`; `deleted` → back to `free` (`test_subscription_lifecycle_updates_plan`)
- An older event arriving late cannot undo a newer state (`test_out_of_order_event_cannot_undo_newer_state`)

**Live: forged signature → 400** (`scripts/probes.py`, signed with a wrong secret):

**forged event** → `POST /webhooks/stripe` → **400** 
```json
{
  "error": {
    "code": "invalid_signature",
    "message": "Webhook signature verification failed. Event rejected."
  }
}
```

**Live: real event replayed → processed once.** Stripe re-delivered the real checkout event with `stripe events resend evt_1URZD4IKhLvwumC6hJSGrxdq` (the last two listener lines above, at 22:08:16 → `[200]`). The same real event fetched from the Stripe API and re-posted with a valid signature answers:
```json
{"received":true,"event_id":"evt_1URZD4IKhLvwumC6hJSGrxdq","status":"duplicate_ignored"}
```
`stripe_events` after both replays: the checkout event is still one row, `attempts = 1`, and `processed_at` is unchanged from the first delivery (15:07:47 UTC):
```text
id              |             type              |  status   | attempts |                                     outcome                                     
------------------------------+-------------------------------+-----------+----------+---------------------------------------------------------------------------------
 evt_1URZD4IKhLvwumC6hJSGrxdq | checkout.session.completed    | processed |        1 | tenant 6034fd33-45c0-4b70-b895-78f277361bed -> pro (checkout completed)
 evt_1URZD5IKhLvwumC66Au1IS35 | customer.subscription.created | processed |        1 | ignored: customer.subscription.created is not handled
 evt_1URZD5IKhLvwumC6Gp7hqqTA | customer.subscription.updated | processed |        1 | tenant 6034fd33-45c0-4b70-b895-78f277361bed -> pro/active (subscription active)
(3 rows)
```

---

## Data model, tests & documentation

### ✅ Database includes tenants, plans, subscriptions and usage events; data isolated per tenant

Schema is versioned migrations in `migrations/` (`001_initial_schema.sql`, `002_stripe_events.sql`, `003_idempotent_response_as_text.sql`), applied automatically on boot. Tables: `plans`, `tenants`, `subscriptions`, `usage_events`, `idempotency_keys`, `stripe_events`. Every query is scoped by the tenant id resolved from `X-API-Key` (`app/api/deps.py`). Proof of isolation: `test_usage_rollup_is_isolated_per_tenant` and `test_same_key_in_two_tenants_is_two_events`. The per-tenant event counts above show Acme's 1,000 calls never touched Globex.

### ✅ README + architecture diagram + setup instructions; required files present

`README.md` (diagram, run + seed steps, limitations), `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`, `docs/DESIGN.md`.

---

## Shared requirements

| # | Requirement | Where |
|---|---|---|
| 1 | Layered architecture | `app/api` (HTTP) → `app/services` (logic) → `app/repo` (SQL) |
| 2 | Validation at the boundary | Pydantic + custom handlers: bad input → `400/401/422`, never `500` (`test_validation_is_clean_4xx`) |
| 3 | ≥1 background job | `worker` service (`app/worker.py`): applies Stripe events off the request path, exponential backoff, `ALERT` log after 5 failures (`test_failing_event_retries_then_alerts`) |
| 4 | Real persistence | SQL migrations, rollup index `(tenant_id, period_start)`, unique `(tenant_id, idempotency_key)`, tenant FKs |
| 5 | Idempotency where it matters | Metering (Idempotency-Key) and webhooks (Stripe event id as primary key) |
| 6 | Secrets clean | `.env` git-ignored, `.env.example` placeholders, live keys refused (`sk_test_` only), secrets never logged |
| 7 | Cost tracked if AI is used | AI usage is simulated, but every request is priced and attributed to a tenant; quotas are the budget guard |
