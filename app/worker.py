"""Background job: apply queued Stripe events off the request path.

Each event is retried with exponential backoff (2s, 4s, 8s, ...). After MAX_ATTEMPTS failures it is
marked 'failed' and an ALERT line is logged so an operator can replay it.

Usage: python -m app.worker
"""
import logging
import time

from app.db import close_pool, transaction
from app.repo import billing as repo
from app.services import stripe_sync

MAX_ATTEMPTS = 5
POLL_SECONDS = 1.0

log = logging.getLogger("worker")


def process_one() -> bool:
    """Process one due event. Returns False when the queue has nothing due."""
    failed = None
    try:
        with transaction() as conn:
            event = repo.claim_next_pending(conn)
            if event is None:
                return False
            try:
                outcome = stripe_sync.apply(conn, event["payload"])
                repo.mark_processed(conn, event["id"], outcome)
                log.info("processed %s %s: %s", event["id"], event["type"], outcome)
                return True
            except Exception as exc:  # noqa: BLE001 - any failure becomes a retry
                failed = (event, exc)
                raise
    except Exception:
        if failed is None:
            raise
    # The apply transaction rolled back; record the failed attempt in a fresh one.
    event, exc = failed
    with transaction() as conn:
        state = repo.mark_attempt_failed(conn, event["id"], f"{type(exc).__name__}: {exc}", MAX_ATTEMPTS)
    if state["status"] == "failed":
        log.error("ALERT stripe event %s (%s) failed %d times and needs manual replay: %s",
                  event["id"], event["type"], state["attempts"], exc)
    else:
        log.warning("event %s attempt %d failed, will retry: %s", event["id"], state["attempts"], exc)
    return True


def process_pending() -> int:
    """Drain everything currently due. Used by tests and by the polling loop."""
    n = 0
    while process_one():
        n += 1
    return n


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("worker started, polling every %.1fs", POLL_SECONDS)
    try:
        while True:
            if process_pending() == 0:
                time.sleep(POLL_SECONDS)
    finally:
        close_pool()


if __name__ == "__main__":
    run_forever()
