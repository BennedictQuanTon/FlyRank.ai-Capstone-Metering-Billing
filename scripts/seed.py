"""Create demo tenants with known API keys. Safe to run more than once.

Usage: python -m scripts.seed
"""
from app.db import close_pool, transaction
from app.repo.tenants import hash_api_key

DEMO_TENANTS = [
    ("Acme (Free)", "demo_free_acme", "free"),
    ("Globex (Pro)", "demo_pro_globex", "pro"),
    ("Initech (Free, for checkout)", "demo_free_initech", "free"),
]


def seed() -> None:
    with transaction() as conn:
        for name, key, plan in DEMO_TENANTS:
            conn.execute(
                """INSERT INTO tenants (name, api_key_hash, plan_code) VALUES (%s, %s, %s)
                   ON CONFLICT (api_key_hash) DO NOTHING""",
                (name, hash_api_key(key), plan),
            )
    print("Demo tenants (X-API-Key -> plan):")
    for name, key, plan in DEMO_TENANTS:
        print(f"  {key:<20} {plan:<5} {name}")


if __name__ == "__main__":
    seed()
    close_pool()
