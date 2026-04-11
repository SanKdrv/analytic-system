import asyncio
import time
from collections import deque
from datetime import UTC, datetime

import httpx
from prometheus_client import Counter, Gauge, Histogram

from .config import Settings
from .models import ProbeRecord, RAGConfig, RAGConfigUpdateResult, ServerTarget, SystemOverview

PROBE_REQUESTS = Counter("rag_probe_requests_total", "Total number of synthetic RAG probes sent")
PROBE_ERRORS = Counter("rag_probe_errors_total", "Total number of failed synthetic RAG probes")
PROBE_LATENCY = Histogram("rag_probe_latency_seconds", "Synthetic RAG probe latency in seconds")
PROBE_QUALITY = Gauge("rag_probe_quality_score", "Latest synthetic RAG quality score")
PROBE_SUCCESS = Gauge("rag_probe_success", "Whether the latest synthetic RAG probe succeeded")
RAG_CONFIG_INFO = Gauge(
    "rag_runtime_config",
    "Current RAG runtime configuration values exposed as labels",
    labelnames=("temperature", "top_k", "top_p", "chunk_size", "retriever"),
)


class MonitoringService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rag_config = RAGConfig(
            temperature=settings.initial_rag_temperature,
            top_k=settings.initial_rag_top_k,
            top_p=settings.initial_rag_top_p,
            chunk_size=settings.initial_rag_chunk_size,
            retriever=settings.initial_rag_retriever,
        )
        self.records: deque[ProbeRecord] = deque(maxlen=100)
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=settings.probe_timeout_seconds)
        self._update_rag_config_metric()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_probe_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _run_probe_loop(self) -> None:
        while True:
            await self.run_single_probe()
            await asyncio.sleep(self.settings.probe_interval_seconds)

    async def run_single_probe(self) -> ProbeRecord:
        PROBE_REQUESTS.inc()
        started_at = time.perf_counter()
        payload = {
            "question": self.settings.probe_prompt,
            "debug": True,
            "synthetic_probe": True,
        }
        answer = ""
        error = None
        success = False

        try:
            response = await self._client.post(
                f"{self.settings.rag_backend_url}{self.settings.rag_query_endpoint}",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            answer = self._extract_answer(data)
            success = True
        except Exception as exc:
            PROBE_ERRORS.inc()
            error = str(exc)
            answer = "Stub answer: monitoring pipeline unavailable, using fallback evaluation."

        latency_seconds = max(time.perf_counter() - started_at, 0.001)
        latency_ms = round(latency_seconds * 1000, 2)
        quality_score, matched_keywords = self._score_answer(answer)

        PROBE_LATENCY.observe(latency_seconds)
        PROBE_QUALITY.set(quality_score)
        PROBE_SUCCESS.set(1 if success else 0)

        record = ProbeRecord(
            timestamp=datetime.now(UTC),
            prompt=self.settings.probe_prompt,
            answer=answer,
            latency_ms=latency_ms,
            success=success,
            quality_score=quality_score,
            matched_keywords=matched_keywords,
            error=error,
        )
        self.records.appendleft(record)
        return record

    async def update_rag_config(self, new_config: RAGConfig) -> RAGConfigUpdateResult:
        self.rag_config = new_config
        self._update_rag_config_metric()

        reloaded = False
        message = "Configuration stored locally. Reload endpoint was not reachable."
        try:
            response = await self._client.post(
                f"{self.settings.rag_backend_url}{self.settings.rag_reload_endpoint}",
                json=new_config.model_dump(),
            )
            response.raise_for_status()
            reloaded = True
            message = "Configuration applied and remote RAG backend reload endpoint acknowledged."
        except Exception:
            pass

        return RAGConfigUpdateResult(
            status="ok",
            reloaded=reloaded,
            applied_config=self.rag_config,
            message=message,
        )

    def get_servers(self) -> list[ServerTarget]:
        return [ServerTarget(**target) for target in self.settings.server_targets]

    def get_recent_probes(self, limit: int = 20) -> list[ProbeRecord]:
        return list(self.records)[:limit]

    def get_overview(self) -> SystemOverview:
        probes = self.get_recent_probes()
        successes = [probe for probe in probes if probe.success]
        throughput_rpm = round((len(probes) / max(self.settings.probe_interval_seconds, 1)) * 60, 2) if probes else 0.0
        success_rate = round(len(successes) / len(probes), 3) if probes else 0.0
        avg_quality_score = round(sum(probe.quality_score for probe in probes) / len(probes), 3) if probes else 0.0
        return SystemOverview(
            rag_config=self.rag_config,
            probe_interval_seconds=self.settings.probe_interval_seconds,
            latest_probe=probes[0] if probes else None,
            recent_probes=probes,
            throughput_rpm=throughput_rpm,
            success_rate=success_rate,
            avg_quality_score=avg_quality_score,
            grafana_embed_url=self.settings.grafana_embed_url,
            grafana_dashboard_url=self.settings.grafana_dashboard_url,
            grafana_public_dashboard=self.settings.grafana_public_dashboard,
            servers=self.get_servers(),
        )

    def _score_answer(self, answer: str) -> tuple[float, list[str]]:
        answer_lower = answer.lower()
        matched = [keyword for keyword in self.settings.probe_keywords if keyword in answer_lower]
        if not self.settings.probe_keywords:
            return 1.0, []
        return round(len(matched) / len(self.settings.probe_keywords), 3), matched

    def _extract_answer(self, payload: dict) -> str:
        if isinstance(payload.get("answer"), str):
            return payload["answer"]
        if isinstance(payload.get("response"), str):
            return payload["response"]
        if isinstance(payload.get("data"), dict):
            data = payload["data"]
            if isinstance(data.get("answer"), str):
                return data["answer"]
        return str(payload)

    def _update_rag_config_metric(self) -> None:
        RAG_CONFIG_INFO.clear()
        RAG_CONFIG_INFO.labels(
            temperature=str(self.rag_config.temperature),
            top_k=str(self.rag_config.top_k),
            top_p=str(self.rag_config.top_p),
            chunk_size=str(self.rag_config.chunk_size),
            retriever=self.rag_config.retriever,
        ).set(1)

