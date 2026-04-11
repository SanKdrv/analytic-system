from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    cors_origins_raw: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    rag_backend_url: str = Field(default="http://rag-backend:8081", alias="RAG_BACKEND_URL")
    rag_reload_endpoint: str = Field(default="/api/admin/reload", alias="RAG_RELOAD_ENDPOINT")
    rag_query_endpoint: str = Field(default="/api/query", alias="RAG_QUERY_ENDPOINT")

    rag_server_name: str = Field(default="rag-core", alias="RAG_SERVER_NAME")
    rag_exporter_target: str = Field(default="http://rag-host:9100", alias="RAG_EXPORTER_TARGET")
    ollama_server_name: str = Field(default="ollama-llm", alias="OLLAMA_SERVER_NAME")
    ollama_exporter_target: str = Field(default="http://ollama-host:9100", alias="OLLAMA_EXPORTER_TARGET")
    embedding_server_name: str = Field(default="embedding-worker", alias="EMBEDDING_SERVER_NAME")
    embedding_exporter_target: str = Field(default="http://embedding-host:9100", alias="EMBEDDING_EXPORTER_TARGET")

    probe_interval_seconds: int = Field(default=15, alias="PROBE_INTERVAL_SECONDS")
    probe_timeout_seconds: int = Field(default=10, alias="PROBE_TIMEOUT_SECONDS")
    probe_prompt: str = Field(
        default="What is the status of the monitored RAG system?",
        alias="PROBE_PROMPT",
    )
    probe_expected_keywords: str = Field(default="monitoring,rag,system", alias="PROBE_EXPECTED_KEYWORDS")

    initial_rag_temperature: float = Field(default=0.2, alias="INITIAL_RAG_TEMPERATURE")
    initial_rag_top_k: int = Field(default=5, alias="INITIAL_RAG_TOP_K")
    initial_rag_top_p: float = Field(default=0.9, alias="INITIAL_RAG_TOP_P")
    initial_rag_chunk_size: int = Field(default=512, alias="INITIAL_RAG_CHUNK_SIZE")
    initial_rag_retriever: str = Field(default="hybrid", alias="INITIAL_RAG_RETRIEVER")

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

