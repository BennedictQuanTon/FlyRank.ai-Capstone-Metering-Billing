"""Run the acceptance probes against a live system and print a Markdown transcript.

Usage: python -m scripts.probes [--base-url http://localhost:8000] [--only 1,2,4,5]
Probe 3 (Stripe Checkout) needs a browser; see README "Upgrade with Stripe Checkout".
"""
import argparse
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx

FREE_KEY, PRO_KEY = "demo_free_acme", "demo_pro_globex"
BODY = {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 2500, "reasoning_tokens": 300}


def show(title: str, r: httpx.Response, keys: list[str] | None = None) -> None:
    body = r.json()
    if keys:
        body = {k: body[k] for k in keys if k in body}
    extra = {h: r.headers[h] for h in ("Idempotent-Replayed", "Retry-After") if h in r.headers}
    print(f"**{title}** → `{r.request.method} {r.request.url.path}` → **{r.status_code}** {extra or ''}")
    print("```json\n" + json.dumps(body, indent=2) + "\n```")


def probe1(c: httpx.Client) -> None:
    print("### Probe 1: same request twice, one Idempotency-Key\n")
    key = f"probe1-{uuid.uuid4().hex[:8]}"
    before = c.get("/usage", headers={"X-API-Key": PRO_KEY}).json()["used"]["events"]
    h = {"X-API-Key": PRO_KEY, "Idempotency-Key": key}
    a, b = c.post("/generate", json=BODY, headers=h), c.post("/generate", json=BODY, headers=h)
    show("first", a, ["usage_event_id", "recorded", "request_cost_micros", "usage_after"])
    show("retry (same key)", b, ["usage_event_id", "recorded", "request_cost_micros", "usage_after"])
    after = c.get("/usage", headers={"X-API-Key": PRO_KEY}).json()["used"]["events"]
    print(f"usage events before: {before}, after: {after}, created: **{after - before}**. "
          f"Bodies byte-identical: **{a.content == b.content}**\n")


def probe2(c: httpx.Client) -> None:
    print("### Probe 2: drive a Free tenant to its exact quota\n")
    h = {"X-API-Key": FREE_KEY}
    used = c.get("/usage", headers=h).json()["used"]["api_calls"]
    sent = 0
    while used < 999:
        r = c.post("/generate", json={}, headers={**h, "Idempotency-Key": uuid.uuid4().hex})
        r.raise_for_status()
        used, sent = r.json()["usage_after"]["api_calls"], sent + 1
    print(f"Sent {sent} requests to reach 999/1000 API calls.\n")
    show("request #1000 (lands exactly on the limit)",
         c.post("/generate", json={}, headers={**h, "Idempotency-Key": uuid.uuid4().hex}), ["usage_after", "limits"])
    show("request #1001", c.post("/generate", json={}, headers={**h, "Idempotency-Key": uuid.uuid4().hex}))
    show("GET /usage", c.get("/usage", headers=h), ["plan", "used", "limits", "remaining"])


def probe4(c: httpx.Client) -> None:
    print("### Probe 4: forged webhook signature\n")
    event = {"id": f"evt_forged_{uuid.uuid4().hex[:8]}", "type": "customer.subscription.updated", "created": int(time.time()),
             "data": {"object": {"id": "sub_x", "customer": "cus_x", "status": "active", "metadata": {}}}}
    payload = json.dumps(event).encode()
    t = int(time.time())
    forged = hmac.new(b"whsec_not_the_real_secret", f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    r = c.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": f"t={t},v1={forged}"})
    show("forged event", r)


def probe5(c: httpx.Client) -> None:
    print("### Probe 5: pinned pricing rules vs GET /usage\n")
    show("GET /usage (Pro tenant)", c.get("/usage", headers={"X-API-Key": PRO_KEY}),
         ["plan", "used", "tokens_detail", "cost", "pricing"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--only", default="1,2,4,5")
    args = ap.parse_args()
    probes = {"1": probe1, "2": probe2, "4": probe4, "5": probe5}
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        for n in args.only.split(","):
            probes[n.strip()](client)
