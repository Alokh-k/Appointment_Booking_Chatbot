from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_config_path: str
    database_url: str
    llm_provider: str
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    reminder_lead_minutes: int

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
