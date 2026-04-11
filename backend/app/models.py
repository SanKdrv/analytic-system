from datetime import datetime

from pydantic import BaseModel, Field


class ServerTarget(BaseModel):
    name: str
    metrics_url: str


class RAGConfig(BaseModel):
    temperature: float = Field(ge=0.0, le=2.0)
    top_k: int = Field(ge=1, le=100)
    top_p: float = Field(ge=0.0, le=1.0)
    chunk_size: int = Field(ge=64, le=4096)
    retriever: str


class ProbeRecord(BaseModel):
    timestamp: datetime
    prompt: str
    answer: str
    latency_ms: float
    success: bool
    quality_score: float = Field(ge=0.0, le=1.0)
    matched_keywords: list[str]
    error: str | None = None


class RAGConfigUpdateResult(BaseModel):
    status: str
    reloaded: bool
    applied_config: RAGConfig
    message: str


class SystemOverview(BaseModel):
    rag_config: RAGConfig
    probe_interval_seconds: int
    latest_probe: ProbeRecord | None
    recent_probes: list[ProbeRecord]
    throughput_rpm: float
    success_rate: float
    avg_quality_score: float
    grafana_embed_url: str
    grafana_dashboard_url: str
    grafana_public_dashboard: bool
    servers: list[ServerTarget]

