"""Application settings loaded from the environment or ``src/.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", SRC_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    local_only: bool = True
    access_passcode: str = ""
    session_secret: str = ""
    baidu_api_key: str | None = None
    baidu_secret_key: str | None = None
    data_dir: Path = SRC_ROOT / "data"
    max_upload_mb: int = Field(default=10, ge=1, le=50)
    ocr_mode: Literal["live", "cache"] = "live"
    daily_ocr_limit: int = Field(default=100, ge=0)
    vlm_enabled: bool = False
    vlm_base_url: str = "https://api-inference.modelscope.cn/v1"
    vlm_model: str = ""
    vlm_api_key: str = ""
    vlm_timeout_s: float = Field(default=60, gt=0, le=120)
    daily_vlm_limit: int = Field(default=100, ge=0)
    claim_precheck_enabled: bool = True
    claim_precheck_model: str = "gpt-6.1-sol"
    daily_claim_precheck_limit: int = Field(default=10, ge=0)
    worker_poll_s: float = Field(default=1, gt=0, le=30)
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    @model_validator(mode="after")
    def validate_vision(self):
        if not self.local_only and (not self.access_passcode.strip() or not self.session_secret.strip()):
            raise ValueError("Authenticated deployments require ACCESS_PASSCODE and SESSION_SECRET")
        if (self.vlm_enabled or self.ocr_mode == "live") and not (
                self.vlm_api_key.strip() and self.vlm_model.strip() and self.vlm_base_url.startswith("https://")):
            raise ValueError("VLM_ENABLED requires an HTTPS VLM_BASE_URL, VLM_MODEL and VLM_API_KEY")
        if self.ocr_mode == "live":
            self.vlm_enabled = True
        return self

    @property
    def ocr_configured(self) -> bool:
        return bool(self.baidu_api_key and self.baidu_secret_key)

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, value):
        path = Path(value) if value else SRC_ROOT / "data"
        return path if path.is_absolute() else SRC_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
