-- Verified Stripe webhook events. The primary key is Stripe's own event id,
-- so a replayed event can never be inserted (or applied) twice.
CREATE TABLE stripe_events (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    created         BIGINT NOT NULL,
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'failed')),
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome         TEXT,
    last_error      TEXT,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX stripe_events_pending_idx ON stripe_events (next_attempt_at) WHERE status = 'pending';
