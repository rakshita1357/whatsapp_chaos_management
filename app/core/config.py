"""Runtime settings (env-driven) and YAML seed loaders."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WCM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://wcm:wcm@localhost:5432/wcm"
    data_dir: Path = ROOT / "data"
    config_dir: Path = ROOT / "config"
    dev_auth: bool = False

    vision_model: str = "claude-sonnet-5-5"
    text_model: str = "claude-haiku-5-5"
    assistant_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    zip_max_uncompressed_bytes: int = 2 * 1024**3
    zip_max_files: int = 20000
    image_max_side_px: int = 1600

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str, config_dir: Path | None = None) -> Any:
    """Load `config/<name>.yaml`. Raises FileNotFoundError if the seed is absent."""
    base = config_dir or get_settings().config_dir
    with (base / f"{name}.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
