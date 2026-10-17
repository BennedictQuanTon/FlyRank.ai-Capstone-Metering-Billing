-- Plans, tenants, subscriptions, usage events and idempotency records.
-- Money columns are BIGINT micro-USD (1 USD = 1,000,000). Never floats.

CREATE TABLE plans (
    code               TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    api_call_limit     BIGINT NOT NULL CHECK (api_call_limit >= 0),
    token_limit        BIGINT NOT NULL CHECK (token_limit >= 0),
    monthly_fee_micros BIGINT NOT NULL CHECK (monthly_fee_micros >= 0)
);

INSERT INTO plans (code, name, api_call_limit, token_limit, monthly_fee_micros) VALUES
    ('free', 'Free', 1000,  100000,  0),
    ('pro',  'Pro',  50000, 5000000, 29000000);

CREATE TABLE tenants (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name               TEXT NOT NULL,
    api_key_hash       TEXT NOT NULL UNIQUE,
    plan_code          TEXT NOT NULL REFERENCES plans(code) DEFAULT 'free',
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due')),
    stripe_customer_id TEXT UNIQUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    plan_code              TEXT NOT NULL REFERENCES plans(code),
    status                 TEXT NOT NULL,
    last_event_created     BIGINT NOT NULL DEFAULT 0,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_tenant_idx ON subscriptions (tenant_id);

CREATE TABLE usage_events (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    idempotency_key     TEXT NOT NULL,
    api_calls           BIGINT NOT NULL CHECK (api_calls >= 0),
    input_tokens        BIGINT NOT NULL CHECK (input_tokens >= 0),
    cached_input_tokens BIGINT NOT NULL CHECK (cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens),
    output_tokens       BIGINT NOT NULL CHECK (output_tokens >= 0),
    reasoning_tokens    BIGINT NOT NULL CHECK (reasoning_tokens >= 0),
    period_start        DATE NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Last line of defense against double-counting, even if application code is wrong.
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX usage_events_rollup_idx ON usage_events (tenant_id, period_start);

CREATE TABLE idempotency_keys (
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    response_status INT NOT NULL,
    response_body   JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
