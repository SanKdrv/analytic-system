from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    http_access_log: bool = Field(default=True, alias="HTTP_ACCESS_LOG")
    cors_origins_raw: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    rag_backend_url: str = Field(default="http://rag-backend:8081", alias="RAG_BACKEND_URL")
    rag_api_secret: str = Field(default="", alias="RAG_API_SECRET")
    rag_generate_endpoint: str = Field(default="/recommendations/generate", alias="RAG_GENERATE_ENDPOINT")
    rag_status_endpoint: str = Field(default="/recommendations/status", alias="RAG_STATUS_ENDPOINT")
    rag_get_endpoint: str = Field(default="/recommendations", alias="RAG_GET_ENDPOINT")
    rag_prompt_get_endpoint: str = Field(default="/prompt", alias="RAG_PROMPT_GET_ENDPOINT")
    rag_prompt_put_endpoint: str = Field(default="/prompt", alias="RAG_PROMPT_PUT_ENDPOINT")
    rag_auth_endpoint: str = Field(default="/auth/key", alias="RAG_AUTH_ENDPOINT")

    rag_server_name: str = Field(default="rag-core", alias="RAG_SERVER_NAME")
    rag_exporter_target: str = Field(default="http://rag-host:9100", alias="RAG_EXPORTER_TARGET")
    ollama_server_name: str = Field(default="ollama-llm", alias="OLLAMA_SERVER_NAME")
    ollama_exporter_target: str = Field(default="http://ollama-host:9100", alias="OLLAMA_EXPORTER_TARGET")
    embedding_server_name: str = Field(default="embedding-worker", alias="EMBEDDING_SERVER_NAME")
    embedding_exporter_target: str = Field(default="http://embedding-host:9100", alias="EMBEDDING_EXPORTER_TARGET")

    probe_loop_enabled: bool = Field(default=False, alias="PROBE_LOOP_ENABLED")
    probe_interval_seconds: int = Field(default=15, alias="PROBE_INTERVAL_SECONDS")
    probe_timeout_seconds: int = Field(default=10, alias="PROBE_TIMEOUT_SECONDS")
    probe_prompt: str = Field(
        default="Generate recommendation for test lead",
        alias="PROBE_PROMPT",
    )
    probe_expected_keywords: str = Field(default="recommendation,lead,system", alias="PROBE_EXPECTED_KEYWORDS")
    probe_lead_id: str = Field(default="test-lead-123", alias="PROBE_LEAD_ID")
    probe_recommendation_type: str = Field(default="cold", alias="PROBE_RECOMMENDATION_TYPE")

    initial_rag_prompt_id: int = Field(default=1, alias="INITIAL_RAG_PROMPT_ID")
    initial_rag_prompt: str = Field(default="Generate a personalized recommendation for the lead.", alias="INITIAL_RAG_PROMPT")

    grafana_embed_url: str = Field(default="", alias="GRAFANA_EMBED_URL")
    grafana_dashboard_url: str = Field(default="", alias="GRAFANA_DASHBOARD_URL")
    grafana_public_dashboard: bool = Field(default=False, alias="GRAFANA_PUBLIC_DASHBOARD")

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @computed_field
    @property
    def probe_keywords(self) -> list[str]:
        return [keyword.strip().lower() for keyword in self.probe_expected_keywords.split(",") if keyword.strip()]

    @computed_field
    @property
    def server_targets(self) -> list[dict[str, str]]:
        return [
            {"name": self.rag_server_name, "metrics_url": self.rag_exporter_target},
            {"name": self.ollama_server_name, "metrics_url": self.ollama_exporter_target},
            {"name": self.embedding_server_name, "metrics_url": self.embedding_exporter_target},
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
