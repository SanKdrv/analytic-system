from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from .config import get_settings
from .models import RAGConfig
from .services import MonitoringService

settings = get_settings()
service = MonitoringService(settings)


class ProbeRequest(BaseModel):
    email: str | None = None
    type: str | None = None



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


@app.post("/api/rag/auth")
async def auth_rag(request: ProbeRequest):
    if not settings.rag_api_secret:
        raise HTTPException(status_code=500, detail="RAG_API_SECRET not set")
    if request.email and request.email != settings.rag_api_secret:
        raise HTTPException(status_code=401, detail="Invalid secret")
    try:
        if not service._api_key:
            await service._authenticate()
        return {"api-key": service._api_key}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {exc}")


@app.post("/api/quality/probe")
async def trigger_probe(request: ProbeRequest):
    if not request.email and not request.type:
        # Use default probe
        return await service.run_single_probe()
    if request.type and request.type not in ["cold", "hot", "warm", "after_sale"]:
        raise HTTPException(status_code=400, detail="Invalid type")
    return await service.run_single_probe(email=request.email, probe_type=request.type)


@app.get("/api/servers")
async def servers():
    return {"items": service.get_servers()}

