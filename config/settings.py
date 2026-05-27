"""
Centralized settings — single source of truth for all runtime configuration.

All secrets and environment-specific values are read from environment variables
or .env file. NO hardcoded secrets anywhere in the codebase.

Validation happens at startup; missing required values cause a clean failure
with a descriptive error rather than a runtime panic later.
"""

from __future__ import annotations

from typing import Optional

from pydantic          import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_name:     str = "EvidentRx"
    environment:  str = Field(default="development", pattern="^(development|staging|production)$")
    log_level:    str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    debug:        bool = False
    version:      str = "9.0.0"

    # ── Database ───────────────────────────────────────────────────────────
    database_url:             str = "postgresql+psycopg2://localhost/evidentrx"
    database_echo:            bool = False
    database_pool_size:       int = Field(default=10, ge=1, le=100)
    database_max_overflow:    int = Field(default=20, ge=0, le=200)
    database_pool_pre_ping:   bool = True
    database_pool_recycle:    int = 3600   # seconds

    # ── Auth / JWT ─────────────────────────────────────────────────────────
    jwt_secret_key:           SecretStr = Field(default="CHANGE_ME_IN_PRODUCTION_MIN_32_CHARS")
    jwt_algorithm:            str = "HS256"
    access_token_expire_min:  int = 15
    refresh_token_expire_days: int = 7

    # ── Security ───────────────────────────────────────────────────────────
    secret_signing_key:       SecretStr = Field(default="CHANGE_ME_SIGNING_KEY_32_CHARS_MIN")
    allowed_origins:          list[str] = ["http://localhost:3000"]
    csrf_enabled:             bool = True
    rate_limit_per_minute:    int = 60
    rate_limit_burst:         int = 20

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url:                Optional[str] = None
    redis_max_connections:    int = 10

    # ── Celery / Task Queue ────────────────────────────────────────────────
    celery_broker_url:        Optional[str] = None  # defaults to redis_url
    celery_result_backend:    Optional[str] = None
    task_max_retries:         int = 3
    task_retry_backoff:       int = 60    # seconds

    # ── LLM Providers ──────────────────────────────────────────────────────
    anthropic_api_key:        Optional[SecretStr] = None
    openai_api_key:           Optional[SecretStr] = None
    default_llm_provider:     str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    default_model:            str = "claude-3-5-sonnet-20241022"
    llm_timeout_seconds:      int = 120
    llm_max_tokens:           int = 4096

    # ── Observability ──────────────────────────────────────────────────────
    otlp_endpoint:            Optional[str] = None   # OpenTelemetry collector
    prometheus_enabled:       bool = True
    sentry_dsn:               Optional[SecretStr] = None
    structured_logging:       bool = True

    # ── Storage ────────────────────────────────────────────────────────────
    s3_bucket:                Optional[str] = None
    s3_region:                str = "us-east-1"
    aws_access_key_id:        Optional[SecretStr] = None
    aws_secret_access_key:    Optional[SecretStr] = None

    # ── Compliance / Governance ────────────────────────────────────────────
    audit_retention_days:     int = Field(default=2555, ge=365)   # 7 years
    phi_masking_enabled:      bool = True
    investigation_archive_days: int = Field(default=90, ge=30)
    max_findings_per_case:    int = 10_000

    # ── Feature Flags (bootstrap values) ───────────────────────────────────
    enable_copilot:           bool = True
    enable_graph_intelligence: bool = True
    enable_predictive_risk:   bool = True
    enable_async_tasks:       bool = True

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def validate_jwt_key_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("jwt_secret_key must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def production_safety_checks(self) -> "Settings":
        if self.environment == "production":
            if self.debug:
                raise ValueError("debug must be False in production")
            if self.jwt_secret_key.get_secret_value().startswith("CHANGE_ME"):
                raise ValueError("jwt_secret_key must be changed in production")
            if self.secret_signing_key.get_secret_value().startswith("CHANGE_ME"):
                raise ValueError("secret_signing_key must be changed in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url or "memory://"

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url or "cache+memory://"


settings = Settings()
