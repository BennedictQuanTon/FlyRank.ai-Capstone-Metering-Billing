# Usage Metering & Billing Engine

A backend service that answers three questions for every tenant of a SaaS product: **how much did they use, what does it cost, and have they hit their plan limit?** It stays correct under client retries, duplicate Stripe webhooks, concurrent requests and exact quota boundaries.

FlyRank Internship · Backend Track · Capstone. Python + FastAPI + PostgreSQL + Stripe (test mode). Total cost: $0.

- **Exactly-once metering:** the same request retried with the same `Idempotency-Key` records one usage event and replays a byte-identical response.
- **Honest quota boundaries:** request 1,000 of 1,000 succeeds and request 1,001 gets `402` (Free) or `429` + `Retry-After` (Pro), with a message explaining why.
- **Correct money math:** integer micro-USD only. Cached input tokens are cheaper, reasoning tokens bill as output, and rounding happens once per month.
- **Safe Stripe sync:** signed webhooks (forged → `400`), deduplicated by event id, applied by a background worker with retries and an alert.

## Architecture

```text
                       X-API-Key + Idempotency-Key
Client ──POST /generate──► api (FastAPI)
                            │  app/api ──► app/services/meter.py ──► app/repo (SQL)
                            │    1. lock tenant row (SELECT … FOR UPDATE)
                            │    2. same key? ──yes──► replay stored response (no new event)
                            │    3. quota check ──over──► 402 / 429 + clear message
                            │    4. insert usage_event + store response   (one transaction)
                            ▼
                      PostgreSQL ◄──── GET /usage = rollup(usage_events) → used, limit, cost
                            ▲
Stripe Checkout (test) ─────┼───────────────────────────────────────────────┐
                            │                                               │ signed webhook
              worker (app/worker.py)                       POST /webhooks/stripe
   polls stripe_events (FOR UPDATE SKIP LOCKED)              1. verify signature (forged → 400)
   apply → tenant plan/status; retry 2s,4s,8s…               2. INSERT … ON CONFLICT (evt id) DO NOTHING
   5 failures → status=failed + ALERT log                       (replay → duplicate_ignored)
                                                             3. 200 queued
```

Layers: `app/api` (HTTP, validation) → `app/services` (rules: metering, quota, cost, Stripe sync) → `app/repo` (SQL only). The full design is in [docs/DESIGN.md](docs/DESIGN.md).

## Plans

| Plan | API calls / month | AI tokens / month | Price |
|---|---:|---:|---:|
| Free | 1,000 | 100,000 | $0 |
| Pro | 50,000 | 5,000,000 | $29 / month |

Periods are calendar months in UTC.

## Run it

Requirements: Docker. Nothing else is needed for the core.

```bash
git clone https://github.com/BennedictQuanTon/FlyRank.ai-Capstone-Metering-Billing.git
cd FlyRank.ai-Capstone-Metering-Billing

cp -n .env.example .env; docker compose up --build -d   # run: db + api + worker, migrations apply on boot
docker compose exec -T api python -m scripts.seed        # seed: 3 demo tenants
docker compose exec -T api pytest -q                     # test: 29 tests against real Postgres
docker compose exec -T api python -m scripts.probes      # live acceptance probes 1, 2, 4, 5
```

API docs: http://localhost:8000/docs

Demo tenants (`X-API-Key`): `demo_free_acme` (Free), `demo_pro_globex` (Pro), `demo_free_initech` (Free, used for Checkout).

### Try it

```bash
# Billable action (simulated AI usage, no model is called)
curl -s -X POST localhost:8000/generate \
  -H 'X-API-Key: demo_free_acme' -H 'Idempotency-Key: order-42' -H 'Content-Type: application/json' \
  -d '{"input_tokens":1200,"cached_input_tokens":200,"output_tokens":2500,"reasoning_tokens":300}'

# Send it again: same body, header Idempotent-Replayed: true, still one event
# Usage, limits and cost this month
curl -s localhost:8000/usage -H 'X-API-Key: demo_free_acme'
```

## Upgrade with Stripe Checkout (test mode)

Stripe test mode is free and needs no credit card. Never use a live key; the app refuses anything that isn't `sk_test_`.

1. Create a Stripe account and stay in **test mode**. Go to Developers → API keys and put the `sk_test_…` key into `.env` as `STRIPE_SECRET_KEY`.
2. Install the Stripe CLI and log in: `brew install stripe/stripe-cli/stripe && stripe login`
3. Forward webhooks and copy the printed `whsec_…` into `.env` as `STRIPE_WEBHOOK_SECRET`:
   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe
   ```
4. Reload the env and create the Pro price ($29/month, `lookup_key=pro_monthly`):
   ```bash
   docker compose up -d --force-recreate api worker
   docker compose exec -T api python -m scripts.stripe_setup
   ```
5. Start a Checkout, open `checkout_url`, and pay with card `4242 4242 4242 4242`, any future date, any CVC:
   ```bash
   curl -s -X POST localhost:8000/billing/checkout -H 'X-API-Key: demo_free_initech'
   ```
6. The webhook arrives, the worker applies it (`docker compose logs worker`), and `GET /usage` shows `plan: pro` with the Pro limits.
7. Replay an event to see deduplication: `stripe events resend evt_…` returns `duplicate_ignored`.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/generate` | `X-API-Key`, `Idempotency-Key` | `201` recorded · replay → same body · `402`/`429` over quota · `422` key reused with a different body |
| GET | `/usage` | `X-API-Key` | used / limits / remaining / cost breakdown / pinned pricing |
| POST | `/billing/checkout` | `X-API-Key` | Returns a Stripe Checkout URL for Pro |
| POST | `/webhooks/stripe` | Stripe signature | `400` forged · `200 queued` · `200 duplicate_ignored` |
| GET | `/health` | none | Liveness |

## Pricing (pinned in `app/config.py`)

| Item | Price |
|---|---|
| API call | 500 micro-USD ($0.50 per 1,000) |
| Uncached input tokens | $0.30 per 1M |
| Cached input tokens | $0.03 per 1M |
| Output tokens (reasoning included) | $2.50 per 1M |

`input_tokens` includes `cached_input_tokens` (the provider convention), so cost = `(input − cached) × input rate + cached × cached rate + (output + reasoning) × output rate`. The worked example is in [EVIDENCE.md](EVIDENCE.md).

## Limitations (honest)

- **Per-tenant serialization.** Billable requests for one tenant are serialized by a row lock. That is simple and provably correct, but it caps one tenant's throughput at what a single row lock allows. A high-volume version would use per-period counter rows or a ledger with batched aggregation.
- **Rollup reads raw events.** `GET /usage` sums `usage_events` with an index. That is fine for thousands of events per month; millions would need a materialized monthly aggregate.
- **No overage, invoices or proration.** Usage beyond quota is blocked, not billed (stretch goals).
- **Simulated AI usage.** Token counts come from the request body; no model is called.
- **Rejected requests are not cached** under their idempotency key on purpose, so a retry after upgrading succeeds. A client that wants "the same answer forever" must not reuse the key.
- **The demo API keys are static** and seeded for evaluation. A real system would issue and rotate per-tenant keys.
- **Webhook ordering** relies on `event.created` second precision. Two events for the same subscription within the same second are applied in arrival order.

## Repo map

```text
app/api/        HTTP routers, auth dependency, request validation
app/services/   meter, quota, cost, billing (Checkout), stripe_sync, periods
app/repo/       SQL for tenants, usage, billing
app/worker.py   background job for Stripe events
migrations/     versioned SQL schema
scripts/        seed, stripe_setup, probes
tests/          29 tests (metering, pricing, webhooks)
docs/DESIGN.md  one-page design
EVIDENCE.md     one proof per requirement
BUILDLOG.md     where AI helped, where it was wrong
```
