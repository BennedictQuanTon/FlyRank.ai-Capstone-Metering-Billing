"""Create the Pro product + $29/month price in Stripe TEST mode. Safe to run more than once.

Usage: python -m scripts.stripe_setup
"""
import stripe

from app.config import settings
from app.services.billing import PRO_PRICE_LOOKUP_KEY


def main() -> None:
    key = settings.stripe_secret_key
    if not key.startswith("sk_test_") or "replace_me" in key:
        raise SystemExit("STRIPE_SECRET_KEY must be a real sk_test_ key (test mode only).")
    stripe.api_key = key

    existing = stripe.Price.list(lookup_keys=[PRO_PRICE_LOOKUP_KEY], active=True, limit=1)
    if existing.data:
        print(f"Pro price already exists: {existing.data[0].id}")
        return
    product = stripe.Product.create(name="Metering Engine Pro", description="50,000 API calls + 5M AI tokens / month")
    price = stripe.Price.create(product=product.id, unit_amount=2900, currency="usd",
                                recurring={"interval": "month"}, lookup_key=PRO_PRICE_LOOKUP_KEY)
    print(f"Created product {product.id} and price {price.id} (lookup_key={PRO_PRICE_LOOKUP_KEY})")


if __name__ == "__main__":
    main()
