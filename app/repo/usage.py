"""Data access for usage events and stored idempotent responses."""


def insert_event(conn, tenant_id, idempotency_key: str, period_start, usage: dict) -> int:
    return conn.execute(
        """INSERT INTO usage_events (tenant_id, idempotency_key, api_calls, input_tokens,
               cached_input_tokens, output_tokens, reasoning_tokens, period_start)
           VALUES (%s, %s, 1, %s, %s, %s, %s, %s) RETURNING id""",
        (tenant_id, idempotency_key, usage["input_tokens"], usage["cached_input_tokens"],
         usage["output_tokens"], usage["reasoning_tokens"], period_start),
    ).fetchone()["id"]


def period_totals(conn, tenant_id, period_start) -> dict:
    """Rollup of one tenant's usage events for one period."""
    return conn.execute(
        # sum() of BIGINT returns NUMERIC (Decimal in Python); cast back so money math stays in ints.
        """SELECT count(*)                                   AS events,
                  COALESCE(sum(api_calls), 0)::bigint        AS api_calls,
                  COALESCE(sum(input_tokens), 0)::bigint     AS input_tokens,
                  COALESCE(sum(cached_input_tokens), 0)::bigint AS cached_input_tokens,
                  COALESCE(sum(output_tokens), 0)::bigint    AS output_tokens,
                  COALESCE(sum(reasoning_tokens), 0)::bigint AS reasoning_tokens
           FROM usage_events WHERE tenant_id = %s AND period_start = %s""",
        (tenant_id, period_start),
    ).fetchone()


def get_idempotent_response(conn, tenant_id, key: str):
    return conn.execute(
        "SELECT request_hash, response_status, response_body FROM idempotency_keys WHERE tenant_id = %s AND key = %s",
        (tenant_id, key),
    ).fetchone()


def store_idempotent_response(conn, tenant_id, key: str, request_hash: str, status: int, body: str) -> None:
    """body is the exact serialized response, so a replay is byte-identical to the original."""
    conn.execute(
        "INSERT INTO idempotency_keys (tenant_id, key, request_hash, response_status, response_body) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, key, request_hash, status, body),
    )
