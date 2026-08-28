"""Engine configuration; hosted product policy belongs to the consumer."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = Field(default="sqlite:///.clearagent/clearagent.sqlite", alias="DATABASE_URL")

    allowed_origins: str = Field(default="", alias="CLEARAGENT_ALLOWED_ORIGINS")

    run_inline: bool = Field(default=False, alias="CLEARAGENT_RUN_INLINE")

    deterministic_mode: bool = Field(default=False, alias="CLEARAGENT_DETERMINISTIC_MODE")

    planner_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_PLANNER_MODEL")

    synthetic_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_SYNTHETIC_MODEL")

    task_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_TASK_MODEL")

    judge_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_JUDGE_MODEL")

    reflection_model: str = Field(default="openai:gpt-5.6-luna", alias="CLEARAGENT_REFLECTION_MODEL")

    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    gepa_max_tokens: int = Field(default=4000, ge=256, le=8_000, alias="CLEARAGENT_GEPA_MAX_TOKENS")

    task_max_tokens: int = Field(default=4000, ge=256, le=8_000, alias="CLEARAGENT_TASK_MAX_TOKENS")

    max_concurrency: int = Field(default=4, ge=1, le=8, alias="CLEARAGENT_PIPELINE_MAX_CONCURRENCY")

    reasoning_effort: str = Field(default="none", alias="CLEARAGENT_PIPELINE_REASONING_EFFORT")

    provider_sort: str = Field(default="throughput", alias="CLEARAGENT_OPENROUTER_PROVIDER_SORT")

    promotion_margin: float = Field(default=0.03, ge=0, le=0.5, alias="CLEARAGENT_PROMOTION_MARGIN")

    debug: bool = Field(default=False, alias="CLEARAGENT_DEBUG")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
