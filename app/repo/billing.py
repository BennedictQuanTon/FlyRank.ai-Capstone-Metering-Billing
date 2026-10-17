"""Data access for Stripe events, subscriptions and tenant plan changes."""
from psycopg.types.json import Jsonb


def insert_event_if_new(conn, event: dict) -> bool:
    """True if this event id was never seen before. Replays hit the primary key and return False."""
    row = conn.execute(
        """INSERT INTO stripe_events (id, type, created, payload) VALUES (%s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING RETURNING id""",
        (event["id"], event["type"], event["created"], Jsonb(event)),
    ).fetchone()
    return row is not None


def claim_next_pending(conn):
    """Lock one due event; SKIP LOCKED lets several workers run without stepping on each other."""
    return conn.execute(
        """SELECT id, type, created, payload, attempts FROM stripe_events
           WHERE status = 'pending' AND next_attempt_at <= now()
           ORDER BY received_at LIMIT 1 FOR UPDATE SKIP LOCKED"""
    ).fetchone()


def mark_processed(conn, event_id: str, outcome: str) -> None:
    conn.execute(
        "UPDATE stripe_events SET status = 'processed', attempts = attempts + 1, outcome = %s, processed_at = now() WHERE id = %s",
        (outcome, event_id),
    )


def mark_attempt_failed(conn, event_id: str, error: str, max_attempts: int) -> dict:
    return conn.execute(
        """UPDATE stripe_events
           SET attempts = attempts + 1,
               last_error = %s,
               status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END,
               next_attempt_at = now() + make_interval(secs => power(2, attempts + 1))
           WHERE id = %s RETURNING attempts, status""",
        (error[:2000], max_attempts, event_id),
    ).fetchone()


def find_tenant_id(conn, tenant_ref: str | None, stripe_customer_id: str | None):
    if tenant_ref:
        row = conn.execute("SELECT id FROM tenants WHERE id::text = %s", (tenant_ref,)).fetchone()
        if row:
            return row["id"]
    if stripe_customer_id:
        row = conn.execute("SELECT id FROM tenants WHERE stripe_customer_id = %s", (stripe_customer_id,)).fetchone()
        if row:
            return row["id"]
    return None


def lock_tenant(conn, tenant_id) -> None:
    conn.execute("SELECT id FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,))


def subscription_last_event(conn, stripe_subscription_id: str) -> int | None:
    row = conn.execute("SELECT last_event_created FROM subscriptions WHERE stripe_subscription_id = %s",
                       (stripe_subscription_id,)).fetchone()
    return row["last_event_created"] if row else None


def upsert_subscription(conn, tenant_id, stripe_subscription_id: str, plan_code: str, status: str, event_created: int) -> None:
    conn.execute(
        """INSERT INTO subscriptions (tenant_id, stripe_subscription_id, plan_code, status, last_event_created)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (stripe_subscription_id) DO UPDATE
           SET plan_code = EXCLUDED.plan_code, status = EXCLUDED.status,
               last_event_created = EXCLUDED.last_event_created, updated_at = now()""",
        (tenant_id, stripe_subscription_id, plan_code, status, event_created),
    )


def set_tenant_plan(conn, tenant_id, plan_code: str, status: str, stripe_customer_id: str | None = None) -> None:
    conn.execute(
        """UPDATE tenants SET plan_code = %s, status = %s,
               stripe_customer_id = COALESCE(%s, stripe_customer_id)
           WHERE id = %s""",
        (plan_code, status, stripe_customer_id, tenant_id),
    )
