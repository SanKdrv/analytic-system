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
    labelnames=("prompt_id", "prompt"),
)


class MonitoringService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rag_config = RAGConfig(
            prompt_id=settings.initial_rag_prompt_id,
            prompt=settings.initial_rag_prompt,
        )
        self.records: deque[ProbeRecord] = deque(maxlen=100)
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=settings.probe_timeout_seconds)
        self._api_key: str | None = None
        self._update_rag_config_metric()

    async def start(self) -> None:
        if self._task is None:
            try:
                await self._authenticate()
            except Exception:
                # Keep the API available even if the remote RAG backend is temporarily unreachable.
                self._api_key = None
            self._task = asyncio.create_task(self._run_probe_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _authenticate(self) -> None:
        if not self.settings.rag_api_secret:
            raise ValueError("RAG_API_SECRET is required")
        try:
            response = await self._client.post(
                f"{self.settings.rag_backend_url}{self.settings.rag_auth_endpoint}",
                json={"secret": self.settings.rag_api_secret},
            )
            response.raise_for_status()
            data = response.json()
            self._api_key = data.get("api-key")
            if not self._api_key:
                raise ValueError("No api-key in auth response")
        except Exception as exc:
            raise ValueError(f"Failed to authenticate with RAG backend: {exc}") from exc

    async def _run_probe_loop(self) -> None:
        while True:
            await self.run_single_probe()
            await asyncio.sleep(self.settings.probe_interval_seconds)

    async def run_single_probe(self) -> ProbeRecord:
        PROBE_REQUESTS.inc()
        started_at = time.perf_counter()
        payload = {
            "lead_id": self.settings.probe_lead_id,
            "type": self.settings.probe_recommendation_type,
        }
        answer = ""
        error = None
        success = False

        try:
            if not self._api_key:
                await self._authenticate()

            # Generate recommendation
            headers = {"Authorization": f"Bearer {self._api_key}"}
            response = await self._client.post(
                f"{self.settings.rag_backend_url}{self.settings.rag_generate_endpoint}",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("token")
            if not token:
                raise ValueError("No token in generate response")

            # Poll status
            status_url = f"{self.settings.rag_backend_url}{self.settings.rag_status_endpoint}/{token}"
            for _ in range(30):  # max 30 polls, ~30 seconds
                await asyncio.sleep(1)
                status_response = await self._client.get(status_url, headers=headers)
                status_response.raise_for_status()
                status_data = status_response.json()
                status = status_data.get("status")
                if status == "completed":
                    break
                elif status == "failed":
                    raise ValueError("Recommendation generation failed")
            else:
                raise ValueError("Recommendation generation timed out")

            # Get recommendation
            get_url = f"{self.settings.rag_backend_url}{self.settings.rag_get_endpoint}/{self.settings.probe_lead_id}"
            get_response = await self._client.get(get_url, headers=headers)
            get_response.raise_for_status()
            get_data = get_response.json()
            recommendations = get_data.get("recommendations", [])
            if recommendations:
                answer = str(recommendations[0].get("data", ""))
            else:
                answer = "No recommendations found"
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
        message = "Configuration stored locally. Prompt update endpoint was not reachable."
        try:
            if not self._api_key:
                await self._authenticate()
            headers = {"Authorization": f"Bearer {self._api_key}"}
            response = await self._client.put(
                f"{self.settings.rag_backend_url}{self.settings.rag_prompt_put_endpoint}",
                json={"id": new_config.prompt_id, "prompt": new_config.prompt},
                headers=headers,
            )
            response.raise_for_status()
            reloaded = True
            message = "Prompt updated successfully."
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
            prompt_id=str(self.rag_config.prompt_id),
            prompt=self.rag_config.prompt[:50],  # truncate for label
        ).set(1)
