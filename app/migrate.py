"""Apply migrations/*.sql in filename order, each exactly once.

Usage: python -m app.migrate [database_url]
"""
import sys
from pathlib import Path

import psycopg

from app.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(url: str) -> list[str]:
    applied = []
    with psycopg.connect(url, autocommit=False) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        conn.commit()
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else settings.database_url
    names = migrate(url)
    print(f"migrations applied: {names or 'none (up to date)'}")
