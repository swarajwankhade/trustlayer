from dataclasses import dataclass, field
from os import getenv


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: getenv("APP_ENV", "development"))
    app_name: str = field(default_factory=lambda: getenv("APP_NAME", "TrustLayer"))
    api_host: str = field(default_factory=lambda: getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: int(getenv("API_PORT", "8000")))
    database_url: str | None = field(default_factory=lambda: getenv("DATABASE_URL"))
    api_key: str | None = field(default_factory=lambda: getenv("API_KEY"))
    redis_url: str = field(default_factory=lambda: getenv("REDIS_URL", "redis://localhost:6379/0"))
    action_rate_limit_per_minute: int = field(default_factory=lambda: int(getenv("ACTION_RATE_LIMIT_PER_MINUTE", "120")))


def get_settings() -> Settings:
    return Settings()
