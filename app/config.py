from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently drop any .env keys not listed below
    )

    # ── Application ───────────────────────────────────────────────────────────
    environment: str  = "development"
    log_level:   str  = "INFO"
    debug:       bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    database_url:            str  = "postgresql+psycopg2://localhost/evidentrx"
    database_echo:           bool = False
    database_pool_size:      int  = 10
    database_max_overflow:   int  = 20
    database_pool_pre_ping:  bool = True

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret_key:     str = "dev_secret_key_change_this_before_any_deploy_32c"
    secret_signing_key: str = "dev_signing_key_change_this_before_any_deploy"

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    redis_url:             str = "redis://localhost:6379/0"
    celery_broker_url:     str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── LLM providers (optional) ──────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key:    str = ""

    # ── AWS (optional) ────────────────────────────────────────────────────────
    aws_access_key_id:     str = ""
    aws_secret_access_key: str = ""
    s3_bucket:             str = ""
    s3_region:             str = "us-east-1"

    # ── Observability (optional) ──────────────────────────────────────────────
    otlp_endpoint: str = ""


settings = Settings()
