-- JSONB normalizes key order, so a replayed response was equal but not byte-identical
-- to the original. Store the exact serialized body instead.
ALTER TABLE idempotency_keys ALTER COLUMN response_body TYPE TEXT USING response_body::text;
