from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    environment: str = Field(default="development", alias="ENVIRONMENT")
    auth_mode: str = Field(default="disabled", alias="AUTH_MODE")
    e2e_api_token: str | None = Field(default=None, alias="E2E_API_TOKEN")
    owner_signing_secret: str | None = Field(default=None, alias="CLEARAGENT_OWNER_SIGNING_SECRET")
    database_url: str = Field(default="sqlite:///.clearagent/clearagent.sqlite", alias="DATABASE_URL")
    allowed_origins: str = Field(default="", alias="CLEARAGENT_ALLOWED_ORIGINS")
    artifact_root: Path = Field(default=Path("/var/data"), alias="ARTIFACT_ROOT")
    run_inline: bool = Field(default=False, alias="CLEARAGENT_RUN_INLINE")
    deterministic_mode: bool = Field(default=False, alias="CLEARAGENT_DETERMINISTIC_MODE")
    data_ttl_seconds: int = Field(default=0, ge=0, le=604_800, alias="CLEARAGENT_DATA_TTL_SECONDS")
    cleanup_interval_seconds: int = Field(default=600, ge=60, le=86_400, alias="CLEARAGENT_CLEANUP_INTERVAL_SECONDS")
    planner_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_PLANNER_MODEL")
    synthetic_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_SYNTHETIC_MODEL")
    task_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_TASK_MODEL")
    judge_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_JUDGE_MODEL")
    reflection_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_REFLECTION_MODEL")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    qdrant_cluster_endpoint: str | None = Field(default=None, alias="QDRANT_CLUSTER_ENDPOINT")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(
        default="clearagent_knowledge_v1",
        alias="CLEARAGENT_QDRANT_COLLECTION",
    )
    embedding_model: str = Field(
        default="qwen/qwen3-embedding-8b",
        alias="CLEARAGENT_EMBEDDING_MODEL",
    )
    embedding_dimensions: int = Field(
        default=4_096,
        ge=32,
        le=8_192,
        alias="CLEARAGENT_EMBEDDING_DIMENSIONS",
    )
    knowledge_chunk_chars: int = Field(
        default=2_400,
        ge=500,
        le=8_000,
        alias="CLEARAGENT_KNOWLEDGE_CHUNK_CHARS",
    )
    knowledge_chunk_overlap_chars: int = Field(
        default=300,
        ge=0,
        le=2_000,
        alias="CLEARAGENT_KNOWLEDGE_CHUNK_OVERLAP_CHARS",
    )
    gepa_max_tokens: int = Field(default=4000, ge=256, le=8_000, alias="CLEARAGENT_GEPA_MAX_TOKENS")
    task_max_tokens: int = Field(default=4000, ge=256, le=8_000, alias="CLEARAGENT_TASK_MAX_TOKENS")
    chat_timeout_seconds: int = Field(
        default=120,
        ge=15,
        le=300,
        alias="CLEARAGENT_CHAT_TIMEOUT_SECONDS",
    )
    max_concurrency: int = Field(default=4, ge=1, le=8, alias="CLEARAGENT_PIPELINE_MAX_CONCURRENCY")
    reasoning_effort: str = Field(default="none", alias="CLEARAGENT_PIPELINE_REASONING_EFFORT")
    provider_sort: str = Field(default="throughput", alias="CLEARAGENT_OPENROUTER_PROVIDER_SORT")
    promotion_margin: float = Field(default=0.03, ge=0, le=0.5, alias="CLEARAGENT_PROMOTION_MARGIN")
    debug: bool = Field(default=False, alias="CLEARAGENT_DEBUG")
    session_builds_per_day: int = Field(default=2, ge=1, le=20, alias="CLEARAGENT_SESSION_BUILDS_PER_DAY")
    global_builds_per_day: int = Field(default=12, ge=1, le=500, alias="CLEARAGENT_GLOBAL_BUILDS_PER_DAY")
    session_plans_per_hour: int = Field(default=12, ge=1, le=100, alias="CLEARAGENT_SESSION_PLANS_PER_HOUR")
    global_plans_per_hour: int = Field(default=120, ge=1, le=5_000, alias="CLEARAGENT_GLOBAL_PLANS_PER_HOUR")
    session_chats_per_hour: int = Field(default=30, ge=1, le=500, alias="CLEARAGENT_SESSION_CHATS_PER_HOUR")
    global_chats_per_hour: int = Field(default=300, ge=1, le=20_000, alias="CLEARAGENT_GLOBAL_CHATS_PER_HOUR")
    session_sources_per_hour: int = Field(default=20, ge=1, le=200, alias="CLEARAGENT_SESSION_SOURCES_PER_HOUR")

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.knowledge_chunk_overlap_chars >= self.knowledge_chunk_chars:
            raise ValueError(
                "CLEARAGENT_KNOWLEDGE_CHUNK_OVERLAP_CHARS must be smaller than "
                "CLEARAGENT_KNOWLEDGE_CHUNK_CHARS"
            )
        qdrant_values = (self.qdrant_cluster_endpoint, self.qdrant_api_key)
        if any(qdrant_values) and not all(qdrant_values):
            raise ValueError(
                "QDRANT_CLUSTER_ENDPOINT and QDRANT_API_KEY must be configured together"
            )
        if self.environment != "production":
            return self
        if self.auth_mode != "token":
            raise ValueError("Production requires AUTH_MODE=token")
        if not self.e2e_api_token or len(self.e2e_api_token) < 32:
            raise ValueError("Production requires an E2E_API_TOKEN of at least 32 characters")
        if not self.database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("Production requires a PostgreSQL DATABASE_URL")
        query = parse_qs(urlsplit(self.database_url).query)
        if query.get("sslmode", [""])[0] not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("Production PostgreSQL must require TLS with sslmode")
        if not self.openrouter_api_key:
            raise ValueError("Production requires OPENROUTER_API_KEY")
        if not self.qdrant_cluster_endpoint or not self.qdrant_api_key:
            raise ValueError("Production requires QDRANT_CLUSTER_ENDPOINT and QDRANT_API_KEY")
        if self.deterministic_mode:
            raise ValueError("Production cannot use deterministic demo mode")
        if self.data_ttl_seconds <= 0:
            raise ValueError("Production requires a positive hosted-data TTL")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def auth_disabled(self) -> bool:
        return self.environment == "development" and self.auth_mode == "disabled"

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def qdrant_configured(self) -> bool:
        return bool(self.qdrant_cluster_endpoint and self.qdrant_api_key)
