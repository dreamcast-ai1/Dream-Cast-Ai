import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .errors import install_error_handlers
from .providers import register_default_providers
from .routers import admin, assets, auth, characters, files, generate, jobs, meta, notifications, projects, references
from .services.worker import WorkerPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs the background worker inside the API process unless WORKER_ENABLED=false (then run `python -m app.worker`)."""
    pool = None
    if get_settings().worker_enabled:
        pool = WorkerPool()
        pool.start()
    yield
    if pool:
        pool.stop()


def create_app() -> FastAPI:
    s = get_settings()
    register_default_providers()
    app = FastAPI(lifespan=lifespan, title="DreamCast AI API", version="0.1.0",
                  docs_url=None if s.is_production else "/docs", redoc_url=None, openapi_url=None if s.is_production else "/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    install_error_handlers(app)
    for r in (meta, auth, projects, characters, references, files, assets, generate, jobs, notifications, admin):
        app.include_router(r.router)
    return app


app = create_app()
