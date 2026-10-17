# Design: Usage Metering & Billing Engine

One page. Written before the code; the code follows it.

## Problem

A SaaS backend has to answer three questions per tenant: how much did they use this month, what does it cost, and are they over their plan limit. The answers must stay correct when clients retry, when Stripe sends the same webhook twice, and when a tenant sits exactly on its quota.

## Plans and quotas (calendar month, UTC)

| Plan | API calls / month | AI tokens / month | Price |
|---|---:|---:|---:|
| Free | 1,000 | 100,000 | $0 |
| Pro | 50,000 | 5,000,000 | $29 / month |

## Data model (Postgres, schema in `migrations/`)

- `plans(code PK, api_call_limit, token_limit, monthly_fee_micros)`
- `tenants(id PK, name, api_key_hash UNIQUE, plan_code FK, status, stripe_customer_id UNIQUE)`, where status is `active | past_due`
- `subscriptions(id PK, tenant_id FK, stripe_subscription_id UNIQUE, plan_code, status, last_event_created)`
- `usage_events(id PK, tenant_id FK, idempotency_key, api_calls, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, period_start, created_at)` with `UNIQUE(tenant_id, idempotency_key)` and an index on `(tenant_id, period_start)`
- `idempotency_keys(tenant_id, key, request_hash, response_status, response_body, PK(tenant_id, key))`
- `stripe_events(id PK = Stripe evt_ id, type, payload, status, attempts, last_error)`

Money is always an integer number of **micro-USD** (1 USD = 1,000,000). There are no floats anywhere in the money path.

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/generate` | `X-API-Key` + `Idempotency-Key` | The one billable action. The body carries simulated token counts. |
| GET | `/usage` | `X-API-Key` | Monthly rollup: used, limit, remaining, cost breakdown |
| POST | `/billing/checkout` | `X-API-Key` | Creates a Stripe Checkout session for Pro (test mode) |
| POST | `/webhooks/stripe` | Stripe signature | Verify, deduplicate, queue |
| GET | `/health` | none | Liveness |

## Idempotency strategy

Every `POST /generate` runs in one transaction:

1. `SELECT … FOR UPDATE` on the tenant row. This serializes billable requests per tenant, so two concurrent requests can neither double-count nor both squeeze under the quota.
2. Look up `(tenant_id, Idempotency-Key)`.
   - Same key and same body hash: return the stored response with `Idempotent-Replayed: true`. No new event is written.
   - Same key and a different body: `422`.
3. Check the quota, insert one `usage_event` and the stored response, then commit.

Only successful responses are stored. A rejected request (429/402) is re-evaluated on retry, so it can succeed after an upgrade. The `UNIQUE(tenant_id, idempotency_key)` constraint is the last line of defense if the code is ever wrong.

## Quota rule (exact boundary)

A request is allowed when `used + requested <= limit`. The 1,000th call on Free succeeds and the 1,001st is blocked.

| Situation | Status | Why |
|---|---|---|
| Free tenant would exceed a limit | `402 Payment Required` | Upgrading to Pro unlocks it |
| Pro tenant would exceed a limit | `429 Too Many Requests` + `Retry-After` | Nothing to buy; wait for the next period |
| Tenant `past_due` (payment failed) | `402 Payment Required` | Fix payment first |

Every block returns `{"error": {"code", "message", "used", "limit", "requested"}}`.

## Token pricing rules (pinned in `app/config.py`)

- `input_tokens` is the total prompt and **includes** `cached_input_tokens`, the same convention as the Gemini and OpenAI usage objects. Validation rejects `cached > input`.
- Uncached input is billed at the input rate, cached input at the cheaper cached rate, and `output + reasoning` at the output rate.
- The categories overlap, so `input + cached + output + reasoning` is **not** a meaningful total. Billable tokens for the quota are `input + output + reasoning`.
- Monthly cost is computed from the month's token totals and rounded half-up to whole micro-USD **once**, so per-request rounding cannot drift.

## Stripe sync

- Checkout uses `client_reference_id = tenant_id` and puts `tenant_id` into the subscription metadata.
- The webhook verifies the signature (forged → `400`), then runs `INSERT … ON CONFLICT (id) DO NOTHING` into `stripe_events` (a replay is acknowledged and ignored), then returns `200`.
- A **background worker** applies queued events off the request path. It retries with backoff, and after 5 failures it marks the event `failed` and logs an `ALERT`.
- Events older than the last one applied to a subscription (`event.created`) are skipped, so out-of-order delivery cannot downgrade a tenant.

## Non-goal

No invoicing, proration or overage billing. Usage beyond the quota is blocked, not charged. Payment truth lives at Stripe; this service only mirrors it.
