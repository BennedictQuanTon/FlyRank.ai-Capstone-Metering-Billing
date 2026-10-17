"""Stripe Checkout (test mode only)."""
import stripe

from app.config import settings
from app.errors import AppError

PRO_PRICE_LOOKUP_KEY = "pro_monthly"


def _client() -> None:
    key = settings.stripe_secret_key
    if not key or "replace_me" in key:
        raise AppError(503, "stripe_not_configured", "Set STRIPE_SECRET_KEY (a sk_test_ key) in .env.")
    if not key.startswith("sk_test_"):
        raise AppError(503, "stripe_live_key_refused", "Only Stripe test-mode keys (sk_test_) are allowed.")
    stripe.api_key = key


def pro_price_id() -> str:
    _client()
    prices = stripe.Price.list(lookup_keys=[PRO_PRICE_LOOKUP_KEY], active=True, limit=1)
    if not prices.data:
        raise AppError(503, "stripe_price_missing", "No Pro price in Stripe yet. Run: python -m scripts.stripe_setup")
    return prices.data[0].id


def create_pro_checkout(tenant: dict) -> dict:
    if tenant["plan_code"] == "pro" and tenant["status"] == "active":
        raise AppError(409, "already_pro", "This tenant is already on an active Pro plan.")
    price_id = pro_price_id()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(tenant["id"]),
        subscription_data={"metadata": {"tenant_id": str(tenant["id"])}},
        customer=tenant["stripe_customer_id"] or None,
        success_url=f"{settings.public_base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.public_base_url}/billing/cancel",
    )
    return {"checkout_url": session.url, "session_id": session.id}
