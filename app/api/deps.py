from fastapi import Header

from app.db import transaction
from app.errors import AppError
from app.repo import tenants as tenants_repo


def current_tenant(x_api_key: str | None = Header(default=None)) -> dict:
    """Resolve the tenant from X-API-Key. Every downstream query is scoped to this tenant only."""
    if not x_api_key:
        raise AppError(401, "missing_api_key", "Send your tenant API key in the X-API-Key header.")
    with transaction() as conn:
        tenant = tenants_repo.get_by_api_key(conn, x_api_key)
    if tenant is None:
        raise AppError(401, "invalid_api_key", "X-API-Key does not match any tenant.")
    return tenant
