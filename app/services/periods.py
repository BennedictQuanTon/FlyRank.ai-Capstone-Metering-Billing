from datetime import date, datetime, timezone


def period_start(now: datetime | None = None) -> date:
    """Billing periods are calendar months in UTC."""
    now = now or datetime.now(timezone.utc)
    return date(now.year, now.month, 1)


def next_period_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def seconds_until_reset(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(1, int((next_period_start(now) - now).total_seconds()))
