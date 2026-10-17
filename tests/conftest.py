import os
import uuid

# Point the app at the test database before anything imports app.config.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://billing:billing@localhost:5433/billing_test")
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret_for_unit_tests"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import transaction  # noqa: E402
from app.main import app  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.repo import tenants as tenants_repo  # noqa: E402
from app.services.periods import period_start  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    migrate(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean_db():
    with transaction() as conn:
        conn.execute("TRUNCATE tenants, usage_events, idempotency_keys, subscriptions CASCADE")
        if conn.execute("SELECT to_regclass('stripe_events')").fetchone()["to_regclass"]:
            conn.execute("TRUNCATE stripe_events")
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_tenant():
    def _make(plan: str = "free", name: str = "t"):
        key = f"key_{uuid.uuid4().hex}"
        with transaction() as conn:
            tenant_id = tenants_repo.create(conn, name, key, plan)
        return {"id": tenant_id, "key": key}
    return _make


def preload_usage(tenant_id, api_calls: int, tokens_per_call: int = 0) -> None:
    """Insert existing usage rows directly so boundary tests don't need 1,000 HTTP calls."""
    with transaction() as conn:
        conn.execute(
            """INSERT INTO usage_events (tenant_id, idempotency_key, api_calls, input_tokens, cached_input_tokens,
                   output_tokens, reasoning_tokens, period_start)
               SELECT %s, 'preload-' || g, 1, %s, 0, 0, 0, %s FROM generate_series(1, %s) g""",
            (tenant_id, tokens_per_call, period_start(), api_calls),
        )


def count_events(tenant_id) -> int:
    with transaction() as conn:
        return conn.execute("SELECT count(*) AS n FROM usage_events WHERE tenant_id = %s", (tenant_id,)).fetchone()["n"]
