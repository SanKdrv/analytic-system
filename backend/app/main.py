from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from .config import get_settings
from .models import RAGConfig
from .services import MonitoringService

settings = get_settings()
service = MonitoringService(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.start()
    yield
    await service.stop()


app = FastAPI(title="RAG Analytics Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/metrics", make_asgi_app())


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/overview")
async def overview():
    return service.get_overview()


@app.get("/api/rag/config")
async def get_rag_config():
    return service.rag_config


@app.put("/api/rag/config")
async def update_rag_config(config: RAGConfig):
    return await service.update_rag_config(config)


@app.get("/api/quality/live")
async def quality_live(limit: int = 20):
    return {"items": service.get_recent_probes(limit=limit)}


@app.post("/api/quality/probe")
async def trigger_probe():
    return await service.run_single_probe()


@app.get("/api/servers")
async def servers():
    return {"items": service.get_servers()}

