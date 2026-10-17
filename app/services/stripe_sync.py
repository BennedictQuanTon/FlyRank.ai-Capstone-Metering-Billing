"""Apply verified Stripe events to our mirror of plan state. Payment truth lives at Stripe.

Returns a short outcome string; anything we deliberately skip is still 'processed' so it is not retried.
"""
from app.repo import billing as repo

HANDLED = {"checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted"}

# Stripe subscription status -> (our plan, our tenant status). None = leave the tenant unchanged.
STATUS_MAP = {
    "active": ("pro", "active"),
    "trialing": ("pro", "active"),
    "past_due": ("pro", "past_due"),
    "unpaid": ("pro", "past_due"),
    "canceled": ("free", "active"),
    "incomplete_expired": ("free", "active"),
    "incomplete": None,
    "paused": ("free", "active"),
}


def apply(conn, event: dict) -> str:
    if event["type"] not in HANDLED:
        return f"ignored: {event['type']} is not handled"
    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        return _checkout_completed(conn, event, obj)
    return _subscription_changed(conn, event, obj)


def _checkout_completed(conn, event: dict, session: dict) -> str:
    if session.get("mode") != "subscription" or not session.get("subscription"):
        return "ignored: not a subscription checkout"
    tenant_id = repo.find_tenant_id(conn, session.get("client_reference_id"), session.get("customer"))
    if tenant_id is None:
        return "ignored: checkout does not reference a known tenant"
    repo.lock_tenant(conn, tenant_id)
    sub_id = session["subscription"]
    last = repo.subscription_last_event(conn, sub_id)
    if last is not None and last > event["created"]:
        return f"skipped: newer event already applied to {sub_id}"
    repo.upsert_subscription(conn, tenant_id, sub_id, "pro", "active", event["created"])
    repo.set_tenant_plan(conn, tenant_id, "pro", "active", stripe_customer_id=session.get("customer"))
    return f"tenant {tenant_id} -> pro (checkout completed)"


def _subscription_changed(conn, event: dict, sub: dict) -> str:
    tenant_ref = (sub.get("metadata") or {}).get("tenant_id")
    tenant_id = repo.find_tenant_id(conn, tenant_ref, sub.get("customer"))
    if tenant_id is None:
        return "ignored: subscription does not reference a known tenant"
    repo.lock_tenant(conn, tenant_id)

    last = repo.subscription_last_event(conn, sub["id"])
    if last is not None and last > event["created"]:
        return f"skipped: out-of-order event older than the last applied one for {sub['id']}"

    status = "canceled" if event["type"] == "customer.subscription.deleted" else sub.get("status", "")
    mapped = STATUS_MAP.get(status)
    if mapped is None:
        repo.upsert_subscription(conn, tenant_id, sub["id"], "pro", status or "unknown", event["created"])
        return f"recorded: subscription {sub['id']} status {status!r}, tenant plan unchanged"
    plan, tenant_status = mapped
    repo.upsert_subscription(conn, tenant_id, sub["id"], "pro", status, event["created"])
    repo.set_tenant_plan(conn, tenant_id, plan, tenant_status, stripe_customer_id=sub.get("customer"))
    return f"tenant {tenant_id} -> {plan}/{tenant_status} (subscription {status})"
