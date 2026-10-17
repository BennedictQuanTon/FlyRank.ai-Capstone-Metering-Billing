"""Data access for tenants and their plans. Every query is scoped by tenant id."""
import hashlib


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


TENANT_WITH_PLAN = """
    SELECT t.id, t.name, t.plan_code, t.status, t.stripe_customer_id,
           p.name AS plan_name, p.api_call_limit, p.token_limit, p.monthly_fee_micros
    FROM tenants t JOIN plans p ON p.code = t.plan_code
"""


def get_by_api_key(conn, api_key: str):
    return conn.execute(TENANT_WITH_PLAN + " WHERE t.api_key_hash = %s", (hash_api_key(api_key),)).fetchone()


def get_for_update(conn, tenant_id):
    """Lock the tenant row for the rest of the transaction (serializes billable requests per tenant)."""
    conn.execute("SELECT id FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,))
    return conn.execute(TENANT_WITH_PLAN + " WHERE t.id = %s", (tenant_id,)).fetchone()


def get(conn, tenant_id):
    return conn.execute(TENANT_WITH_PLAN + " WHERE t.id = %s", (tenant_id,)).fetchone()


def create(conn, name: str, api_key: str, plan_code: str = "free"):
    return conn.execute(
        "INSERT INTO tenants (name, api_key_hash, plan_code) VALUES (%s, %s, %s) RETURNING id",
        (name, hash_api_key(api_key), plan_code),
    ).fetchone()["id"]
