import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .errors import install_error_handlers
from .providers import register_default_providers
from .routers import admin, assets, auth, characters, files, generate, jobs, meta, notifications, google_auth, payment_webhook, projects, references, scenes, subscription, support, text
from .services.worker import WorkerPool
from .storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs the background worker inside the API process unless WORKER_ENABLED=false (then run `python -m app.worker`)."""
    storage = get_storage()      # local: creates the folders (a fresh disk starts empty); object storage: just builds the client
    s = get_settings()
    if s.is_production and (s.database_url.startswith("sqlite") or s.storage_backend == "local"):
        logging.getLogger("dreamcast").warning("Production is using %s: user data is NOT permanent on a host with an ephemeral disk. "
                                               "Set DATABASE_URL to PostgreSQL and STORAGE_BACKEND=s3.",
                                               " and ".join(x for x, on in (("SQLite", s.database_url.startswith("sqlite")), ("local file storage", s.storage_backend == "local")) if on))
    if s.storage_backend == "s3":
        ok, message = storage.ping()
        logging.getLogger("dreamcast").log(logging.INFO if ok else logging.ERROR, "object storage check: %s", message)
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
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"], max_age=600)
    install_error_handlers(app)

    @app.get("/health", include_in_schema=False)
    def health():
        """Unauthenticated liveness probe for Render/uptime monitors. Touches nothing (no DB, no providers)."""
        return {"status": "ok"}

    for r in (meta, auth, google_auth, projects, characters, references, files, assets, generate, jobs, notifications, subscription, payment_webhook, scenes, text, support, admin):
        app.include_router(r.router)
    app.include_router(support.admin_router)
    return app


app = create_app()
