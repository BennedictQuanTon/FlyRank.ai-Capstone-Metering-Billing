from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://billing:billing@localhost:5433/billing"
    test_database_url: str = "postgresql://billing:billing@localhost:5433/billing_test"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    public_base_url: str = "http://localhost:8000"


settings = Settings()
