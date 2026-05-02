import asyncio
import time
from collections import deque
from datetime import UTC, datetime
import logging

import httpx
from prometheus_client import Counter, Gauge, Histogram

from .config import Settings
from .models import ProbeRecord, RAGConfig, RAGConfigUpdateResult, ServerTarget, SystemOverview

# Mapping prompt_id to lead_type for RAG backend API
PROMPT_ID_TO_LEAD_TYPE = {
    1: "cold",
    2: "warm",
    3: "hot",
    4: "after_sale",
}

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

logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rag_config = RAGConfig(
            prompt_id=settings.initial_rag_prompt_id,
            prompt=settings.initial_rag_prompt,
        )
        self.records: deque[ProbeRecord] = deque(maxlen=100)
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=settings.probe_timeout_seconds, follow_redirects=True)
        self._api_key: str | None = None
        self._update_rag_config_metric()

    async def start(self) -> None:
        if self._task is None:
            logger.info("event=monitoring.start")
            try:
                await self._authenticate()
            except Exception as exc:
                # Keep the API available even if the remote RAG backend is temporarily unreachable.
                self._api_key = None
                logger.warning("event=rag.auth.startup_failed error=%s", exc)
            self._task = asyncio.create_task(self._run_probe_loop())
            logger.info("event=monitoring.probe_loop.created")

    async def stop(self) -> None:
        if self._task is not None:
            logger.info("event=monitoring.probe_loop.cancel")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        logger.info("event=monitoring.stopped")

    async def _authenticate(self) -> None:
        if not self.settings.rag_api_secret:
            raise ValueError("RAG_API_SECRET is required")
        auth_url = f"{self.settings.rag_backend_url.rstrip('/')}/{self.settings.rag_auth_endpoint.lstrip('/')}"
        try:
            logger.info(
                "event=rag.auth.start url=%s timeout_seconds=%s",
                auth_url,
                self.settings.probe_timeout_seconds,
            )
            response = await self._client.post(
                auth_url,
                json={"secret": self.settings.rag_api_secret},
                headers={"Accept": "application/json"},
            )
            if response.is_error:
                logger.warning(
                    "event=rag.auth.http_error status_code=%s response_body=%s",
                    response.status_code,
                    response.text[:500],
                )
                response.raise_for_status()
            data = response.json()
            self._api_key = data.get("api-key") or data.get("api_key") or data.get("key") or data.get("token")
            if not self._api_key:
                logger.warning(
                    "event=rag.auth.missing_api_key status_code=%s response_keys=%s response_body=%s",
                    response.status_code,
                    ",".join(data.keys()),
                    response.text[:500],
                )
                raise ValueError("No api-key in auth response")
            logger.info(
                "event=rag.auth.done status_code=%s response_keys=%s",
                response.status_code,
                ",".join(data.keys()),
            )
        except Exception as exc:
            logger.exception("event=rag.auth.error")
            raise ValueError(f"Failed to authenticate with RAG backend: {exc}") from exc

    async def _run_probe_loop(self) -> None:
        while True:
            try:
                await self.run_single_probe()
            except Exception:
                logger.exception("event=probe.loop.unhandled_error")
            logger.debug(
                "event=probe.loop.sleep interval_seconds=%s",
                self.settings.probe_interval_seconds,
            )
            await asyncio.sleep(self.settings.probe_interval_seconds)

    async def run_single_probe(self, email: str | None = None, probe_type: str | None = None) -> ProbeRecord:
        PROBE_REQUESTS.inc()
        started_at = time.perf_counter()
        
        # Use provided email/type or fallback to settings
        lead_id = self.settings.probe_lead_id
        recommendation_type = probe_type or self.settings.probe_recommendation_type
        
        error = None
        if email:
            # Check contact uniqueness in Mautic
            try:
                if not self._api_key:
                    await self._authenticate()
                headers = {"Authorization": f"Bearer {self._api_key}"}
                logger.info("event=mautic.contact_check.start email_provided=true")
                check_response = await self._client.get(
                    f"{self.settings.rag_backend_url}/mautic/contact/check",
                    params={"email": email},
                    headers=headers,
                )
                check_response.raise_for_status()
                check_data = check_response.json()
                if check_data.get("unique"):
                    lead_id = str(check_data["contact_id"])
                    logger.info("event=mautic.contact_check.done lead_id=%s", lead_id)
                else:
                    error = "Email not unique in Mautic"
                    logger.warning("event=mautic.contact_check.not_unique")
            except Exception as exc:
                error = f"Failed to check contact: {exc}"
                logger.exception("event=mautic.contact_check.error")
        
        payload = {
            "lead_id": lead_id,
            "type": recommendation_type,
        }
        answer = ""
        success = False
        logger.info(
            "event=probe.start lead_id=%s type=%s email_provided=%s",
            lead_id,
            recommendation_type or "-",
            bool(email),
        )

        if error:
            # If contact check failed, skip probe and return error record
            PROBE_ERRORS.inc()
            answer = f"Error: {error}"
        else:
            try:
                if not self._api_key:
                    await self._authenticate()

                # Generate recommendation
                headers = {"Authorization": f"Bearer {self._api_key}"}
                logger.info(
                    "event=rag.generate.start url=%s%s lead_id=%s type=%s",
                    self.settings.rag_backend_url,
                    self.settings.rag_generate_endpoint,
                    payload["lead_id"],
                    payload["type"] or "-",
                )
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
                logger.info("event=rag.generate.done status_code=%s token=%s", response.status_code, token)

                # Poll status with timeout
                import time as time_module
                start_poll_time = time_module.time()
                poll_timeout = 300  # 5 minutes max
                poll_interval = 5   # poll every 5 seconds
                status_url = f"{self.settings.rag_backend_url}{self.settings.rag_status_endpoint}/{token}"
                
                while time_module.time() - start_poll_time < poll_timeout:
                    await asyncio.sleep(poll_interval)
                    logger.info("event=rag.status.poll token=%s", token)
                    status_response = await self._client.get(status_url, headers=headers)
                    status_response.raise_for_status()
                    status_data = status_response.json()
                    status = status_data.get("status")
                    logger.info(
                        "event=rag.status.result token=%s status=%s status_code=%s",
                        token,
                        status,
                        status_response.status_code,
                    )
                    if status == "completed":
                        break
                    elif status == "failed":
                        raise ValueError("Recommendation generation failed")
                else:
                    raise ValueError("Recommendation generation timed out")

                # Get recommendation
                get_url = f"{self.settings.rag_backend_url}/recommendations/{lead_id}"
                logger.info("event=rag.recommendation_get.start lead_id=%s", lead_id)
                get_response = await self._client.get(get_url, headers=headers)
                get_response.raise_for_status()
                get_data = get_response.json()
                recommendations = get_data.get("recommendations", [])
                if recommendations:
                    answer = str(recommendations[0].get("data", ""))
                else:
                    answer = "No recommendations found"
                logger.info(
                    "event=rag.recommendation_get.done lead_id=%s status_code=%s recommendations_count=%s",
                    lead_id,
                    get_response.status_code,
                    len(recommendations),
                )
                success = True
            except Exception as exc:
                PROBE_ERRORS.inc()
                error = str(exc)
                answer = "Stub answer: monitoring pipeline unavailable, using fallback evaluation."
                logger.exception("event=probe.error lead_id=%s type=%s", lead_id, recommendation_type or "-")

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
        logger.info(
            "event=probe.done lead_id=%s type=%s success=%s latency_ms=%s quality_score=%s matched_keywords=%s error=%s",
            lead_id,
            recommendation_type or "-",
            success,
            latency_ms,
            quality_score,
            ",".join(matched_keywords) if matched_keywords else "-",
            error or "-",
        )
        return record

    async def update_rag_config(self, new_config: RAGConfig) -> RAGConfigUpdateResult:
        self.rag_config = new_config
        self._update_rag_config_metric()
        logger.info(
            "event=rag_config.update.start prompt_id=%s prompt_length=%s",
            new_config.prompt_id,
            len(new_config.prompt),
        )

        reloaded = False
        message = "Configuration stored locally. Prompt update endpoint was not reachable."
        try:
            if not self._api_key:
                await self._authenticate()
            headers = {"Authorization": f"Bearer {self._api_key}"}
            lead_type = PROMPT_ID_TO_LEAD_TYPE.get(new_config.prompt_id, "cold")
            logger.info("event=rag_config.remote_update.start lead_type=%s", lead_type)
            response = await self._client.put(
                f"{self.settings.rag_backend_url}{self.settings.rag_prompt_put_endpoint}",
                json={"lead_type": lead_type, "prompt": new_config.prompt},
                headers=headers,
            )
            response.raise_for_status()
            reloaded = True
            message = "Prompt updated successfully."
            logger.info("event=rag_config.remote_update.done status_code=%s", response.status_code)
        except Exception as exc:
            logger.warning("event=rag_config.remote_update.error error=%s", exc)

        logger.info("event=rag_config.update.done reloaded=%s", reloaded)
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
