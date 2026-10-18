"""Application settings loaded from the environment or ``src/.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    access_passcode: str
    session_secret: str
    baidu_api_key: str | None = None
    baidu_secret_key: str | None = None
    data_dir: Path = SRC_ROOT / "data"
    max_upload_mb: int = 10
    ocr_mode: str = "live"

    @field_validator("access_passcode", "session_secret")
    @classmethod
    def reject_blank_secrets(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @property
    def ocr_configured(self) -> bool:
        return bool(self.baidu_api_key and self.baidu_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
