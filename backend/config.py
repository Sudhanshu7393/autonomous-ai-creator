"""
config.py — Centralised application settings via pydantic-settings.

All values can be overridden by environment variables or a .env file.
Validation happens at startup so misconfigured deployments fail fast.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Groq ──────────────────────────────────────────────────────
    groq_api_key: str = Field(default="", description="Groq API key")

    # ── Tavily ────────────────────────────────────────────────────
    tavily_api_key: str = Field(default="", description="Tavily API key for web search")

    # ── Scheduler ─────────────────────────────────────────────────
    # Demo default: 90 s.  Production: 7200 s (2 h) or 10800 s (3 h).
    cycle_interval_seconds: int = Field(
        default=300,
        ge=30,
        description="Seconds between autonomous pipeline cycles (300=5min recommended for free tier).",
    )

    # ── Editorial bar ─────────────────────────────────────────────
    min_relevance_score: float = Field(
        default=6.0,
        ge=0.0,
        le=10.0,
        description="Minimum composite editorial score (0–10) to publish.",
    )
    max_topics_per_cycle: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Maximum candidate topics fetched per discovery cycle.",
    )

    # ── Storage ───────────────────────────────────────────────────
    db_path: str = Field(default="./data/agent.db", description="SQLite file path.")

    # ── API ───────────────────────────────────────────────────────
    cors_origins: str = Field(default="*", description="Comma-separated CORS origins.")

    # ── Logging ───────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging verbosity.")

    # ── Model ─────────────────────────────────────────────────────
    # llama-3.3-70b-versatile is Groq's best general-purpose model.
    groq_model: str = Field(
        default="llama-3.1-8b-instant",
        description="Groq model to use. llama-3.1-8b-instant has 500K TPD free tier.",
    )

    # ── Derived ───────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.db_path))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
