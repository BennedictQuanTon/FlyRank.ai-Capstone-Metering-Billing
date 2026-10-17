# BUILDLOG

How AI was used in this build, where it was wrong, and what changed.

## How I used AI

I used Claude Code as a pair programmer for the whole build. I gave it the capstone brief and made the scoping calls: the stack (FastAPI + Postgres, which I used throughout the track), the repo, and the order of phases. It drafted the design doc, the code, the tests and the docs, phase by phase, and ran the tests and live probes in Docker after each phase. Every phase is a separate commit, so the history shows how the system grew.

Because most of the code was AI-drafted, the rule I hold myself to is the brief's: I must be able to explain any 2–3 lines. The last section lists the lines I consider the core.

## Where AI was wrong, and what changed

| # | What happened | How it was caught | Fix |
|---|---|---|---|
| 1 | `SUM()` over `BIGINT` columns returns `NUMERIC` in Postgres, which psycopg turns into Python `Decimal`. The first version of `/generate` crashed with `TypeError: Object of type Decimal is not JSON serializable`. A float-like type had also sneaked into the money path. | 10 of 12 tests failed on the first run | Cast every aggregate back with `::bigint` in `app/repo/usage.py`, so money math stays in Python `int` |
| 2 | Replayed idempotent responses were *equal* JSON but **not byte-identical**: JSONB normalizes key order, so the retry listed fields in a different order than the original. The brief says the second response must *mirror* the first. | The live Probe 1 transcript showed the reordered keys; the unit test had only compared parsed JSON | Migration `003` stores the exact serialized body as `TEXT`; the test now asserts `first.content == second.content` |


Design traps that were considered and avoided before any code shipped (not bugs, but places where the obvious answer was wrong):

| # | Trap | Decision |
|---|---|---|
| 3 | Two rows per request (one `api_call`, one `ai_tokens`) would make Probe 1 ("exactly one usage event") ambiguous. | One row per billable request, carrying the call and all four token categories |
| 4 | Storing *every* response under the idempotency key, including `402/429`, would leave a tenant who upgrades stuck replaying the old `402` forever. | Only successful responses are stored; `test_rejected_request_is_not_stored_and_can_succeed_later` pins it |
| 5 | Rounding each request's cost and then summing drifts upward: three 2.5-micro requests give 9 instead of 7.5 → 8. | Store raw token counts per event, compute the month's cost from totals, round once (`test_rounding_happens_once_per_period_not_per_request`) |

## Decisions I made myself and why

- **402 vs 429.** Free over quota is `402` because paying fixes it. Pro over quota is `429` with `Retry-After` because there is nothing more to buy; only time fixes it. A failed payment (`past_due`) is `402`.
- **The exact boundary is allowed.** `used + requested <= limit`: the 1,000th call on a 1,000-call plan succeeds.
- **Tenant row lock instead of retries on serialization failures.** It is the simplest thing that makes both double-counting and quota overshoot impossible, and the concurrency tests prove it. The throughput cost is written down under Limitations in the README.
- **The webhook only queues; the worker applies.** The webhook returns fast, and a failing apply is retried without Stripe needing to resend.

## Lines I can explain (the core)

- `app/services/meter.py`: the four numbered steps inside one `transaction()`. The lock comes first, so the idempotency lookup and the quota check see a stable world.
- `migrations/001_initial_schema.sql`: `UNIQUE (tenant_id, idempotency_key)` is the database's guarantee if the code above ever regresses.
- `app/services/cost.py`: `uncached_input = input_tokens - cached_input_tokens` is why categories are split rather than added, and `round_half_up_div` is the only rounding step.
- `app/api/billing.py`: `construct_event` runs on the **raw** body bytes; parsed and re-serialized JSON would not match Stripe's signature.
- `app/repo/billing.py`: `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id` returns nothing for a replayed event, so it is never queued twice.
- `app/repo/billing.py`: `FOR UPDATE SKIP LOCKED` lets two workers run without processing the same event.
