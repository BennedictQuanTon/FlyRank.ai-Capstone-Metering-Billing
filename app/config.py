from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://billing:billing@localhost:5433/billing"
    test_database_url: str = "postgresql://billing:billing@localhost:5433/billing_test"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    public_base_url: str = "http://localhost:8000"


settings = Settings()


# Pricing constants, pinned. All money is integer micro-USD (1 USD = 1,000,000 micros).
# Token rates are per 1,000,000 tokens, shaped like a real provider price sheet
# (cached input is cheaper than fresh input; reasoning/"thinking" tokens are billed at the output rate).
PRICING = {
    "api_call_micros": 500,                   # $0.0005 per call  ($0.50 per 1,000 calls)
    "input_per_mtok_micros": 300_000,         # $0.30 per 1M uncached input tokens
    "cached_input_per_mtok_micros": 30_000,   # $0.03 per 1M cached input tokens
    "output_per_mtok_micros": 2_500_000,      # $2.50 per 1M output tokens (reasoning tokens included)
}
