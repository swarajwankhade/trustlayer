from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    app_env: str = getenv("APP_ENV", "development")
    app_name: str = getenv("APP_NAME", "TrustLayer")
    api_host: str = getenv("API_HOST", "0.0.0.0")
    api_port: int = int(getenv("API_PORT", "8000"))


def get_settings() -> Settings:
    return Settings()
