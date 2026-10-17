import hashlib
import hmac
import json
import time

from app import worker
from app.db import transaction
from app.services import stripe_sync

SECRET = "whsec_test_secret_for_unit_tests"


def signed(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header exactly like Stripe does: HMAC-SHA256 over '{t}.{payload}'."""
    t = timestamp or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def checkout_event(tenant_id, event_id="evt_checkout_1", created=1_000, sub="sub_1", cus="cus_1"):
    return {"id": event_id, "type": "checkout.session.completed", "created": created,
            "data": {"object": {"object": "checkout.session", "mode": "subscription", "client_reference_id": str(tenant_id),
                                "customer": cus, "subscription": sub}}}


def sub_event(tenant_id, etype, status, event_id, created, sub="sub_1", cus="cus_1"):
    return {"id": event_id, "type": etype, "created": created,
            "data": {"object": {"object": "subscription", "id": sub, "customer": cus, "status": status,
                                "metadata": {"tenant_id": str(tenant_id)}}}}


def post(client, event: dict, header: str | None = None):
    payload = json.dumps(event).encode()
    return client.post("/webhooks/stripe", content=payload,
                       headers={"Stripe-Signature": header or signed(payload), "Content-Type": "application/json"})


def tenant_row(tenant_id):
    with transaction() as conn:
        return conn.execute("SELECT plan_code, status, stripe_customer_id FROM tenants WHERE id = %s", (tenant_id,)).fetchone()


def event_rows():
    with transaction() as conn:
        return conn.execute("SELECT id, status, attempts, outcome FROM stripe_events ORDER BY id").fetchall()


def test_forged_signature_is_400_and_changes_nothing(client, make_tenant):
    t = make_tenant("free")
    event = checkout_event(t["id"])
    payload = json.dumps(event).encode()
    r = post(client, event, header=signed(payload, secret="whsec_attacker"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_signature"
    assert client.post("/webhooks/stripe", content=payload).status_code == 400  # no header at all
    worker.process_pending()
    assert event_rows() == []
    assert tenant_row(t["id"])["plan_code"] == "free"


def test_tampered_body_fails_verification(client, make_tenant):
    t = make_tenant("free")
    event = checkout_event(t["id"])
    header = signed(json.dumps(event).encode())
    event["data"]["object"]["client_reference_id"] = "someone-else"
    assert post(client, event, header=header).status_code == 400


def test_checkout_flips_free_to_pro_and_usage_shows_new_limits(client, make_tenant):
    t = make_tenant("free")
    r = post(client, checkout_event(t["id"]))
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert worker.process_pending() == 1
    row = tenant_row(t["id"])
    assert (row["plan_code"], row["status"], row["stripe_customer_id"]) == ("pro", "active", "cus_1")
    usage = client.get("/usage", headers={"X-API-Key": t["key"]}).json()
    assert usage["plan"] == "pro"
    assert usage["limits"] == {"api_calls": 50_000, "tokens": 5_000_000}


def test_replayed_event_is_processed_once(client, make_tenant):
    t = make_tenant("free")
    event = checkout_event(t["id"])
    first, second = post(client, event), post(client, event)
    assert first.json()["status"] == "queued"
    assert second.status_code == 200 and second.json()["status"] == "duplicate_ignored"
    assert worker.process_pending() == 1
    rows = event_rows()
    assert len(rows) == 1 and rows[0]["status"] == "processed" and rows[0]["attempts"] == 1


def test_subscription_lifecycle_updates_plan(client, make_tenant):
    t = make_tenant("free")
    post(client, checkout_event(t["id"], created=100))
    post(client, sub_event(t["id"], "customer.subscription.updated", "past_due", "evt_2", 200))
    worker.process_pending()
    assert (tenant_row(t["id"])["plan_code"], tenant_row(t["id"])["status"]) == ("pro", "past_due")
    post(client, sub_event(t["id"], "customer.subscription.deleted", "canceled", "evt_3", 300))
    worker.process_pending()
    assert (tenant_row(t["id"])["plan_code"], tenant_row(t["id"])["status"]) == ("free", "active")


def test_out_of_order_event_cannot_undo_newer_state(client, make_tenant):
    t = make_tenant("free")
    post(client, checkout_event(t["id"], created=100))
    post(client, sub_event(t["id"], "customer.subscription.deleted", "canceled", "evt_new", 300))
    worker.process_pending()
    # An older 'active' update arrives late; it must not resurrect Pro.
    post(client, sub_event(t["id"], "customer.subscription.updated", "active", "evt_old", 200))
    worker.process_pending()
    assert tenant_row(t["id"])["plan_code"] == "free"
    outcomes = {r["id"]: r["outcome"] for r in event_rows()}
    assert outcomes["evt_old"].startswith("skipped")


def test_unrelated_events_are_acknowledged_and_ignored(client):
    r = post(client, {"id": "evt_x", "type": "invoice.created", "created": 1, "data": {"object": {}}})
    assert r.status_code == 200
    worker.process_pending()
    assert event_rows()[0]["outcome"].startswith("ignored")


def test_failing_event_retries_then_alerts(client, make_tenant, monkeypatch, caplog):
    t = make_tenant("free")
    post(client, checkout_event(t["id"]))

    def boom(conn, event):
        raise RuntimeError("database hiccup")
    monkeypatch.setattr(stripe_sync, "apply", boom)

    for _ in range(worker.MAX_ATTEMPTS):
        with transaction() as conn:  # skip the backoff wait
            conn.execute("UPDATE stripe_events SET next_attempt_at = now()")
        worker.process_pending()

    row = event_rows()[0]
    assert row["status"] == "failed" and row["attempts"] == worker.MAX_ATTEMPTS
    assert any("ALERT" in m for m in caplog.messages)
    assert tenant_row(t["id"])["plan_code"] == "free"
