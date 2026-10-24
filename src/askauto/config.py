"""Application settings via pydantic-settings, read from the environment and .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_path: Path = Path("data/askauto.db")  # relative paths are from the repo root
    schema_mode: Literal["full", "vector"] = "full"  # "vector" arrives in stage 2
    sql_timeout_s: float = 5.0
    sql_max_rows: int = 500

    # LLM provider. The key is optional here so the MCP server runs without it;
    # the provider raises a clear error if it is missing.
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 3

    @property
    def resolved_database_path(self) -> Path:
        path = self.database_path
        return path if path.is_absolute() else REPO_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
