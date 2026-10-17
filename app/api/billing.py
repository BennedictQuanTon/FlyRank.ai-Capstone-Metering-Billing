import json

import stripe
from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import current_tenant
from app.config import settings
from app.db import transaction
from app.errors import AppError
from app.repo import billing as billing_repo
from app.services import billing

router = APIRouter()


@router.post("/billing/checkout", summary="Start a Stripe Checkout (test mode) to upgrade to Pro")
def checkout(tenant: dict = Depends(current_tenant)):
    return billing.create_pro_checkout(tenant)


@router.get("/billing/success", include_in_schema=False)
def success(session_id: str = ""):
    return {"message": "Checkout complete. Your plan updates as soon as the Stripe webhook is processed.",
            "session_id": session_id}


@router.get("/billing/cancel", include_in_schema=False)
def cancel():
    return {"message": "Checkout canceled. Nothing was charged."}


@router.post("/webhooks/stripe", summary="Stripe webhook: verify, deduplicate, queue")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    # Verify against the raw bytes: re-serialized JSON would not match the signature.
    payload = await request.body()
    if not settings.stripe_webhook_secret or "replace_me" in settings.stripe_webhook_secret:
        raise AppError(503, "webhook_not_configured", "Set STRIPE_WEBHOOK_SECRET in .env.")
    if not stripe_signature:
        raise AppError(400, "missing_signature", "Missing Stripe-Signature header.")
    try:
        stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
    except (stripe.SignatureVerificationError, ValueError):
        raise AppError(400, "invalid_signature", "Webhook signature verification failed. Event rejected.")

    event = json.loads(payload)
    with transaction() as conn:
        is_new = billing_repo.insert_event_if_new(conn, event)
    return {"received": True, "event_id": event["id"], "status": "queued" if is_new else "duplicate_ignored"}
