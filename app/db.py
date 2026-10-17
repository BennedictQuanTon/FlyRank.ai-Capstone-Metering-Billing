from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def init_pool(url: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(url or settings.database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction():
    """One connection, one transaction: commits on success, rolls back on any exception."""
    with init_pool().connection() as conn:
        with conn.transaction():
            yield conn
