import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import ingest
from api.routes import health

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """
    Application factory that creates and configures the FastAPI instance.

    Registers CORS middleware, mounts the ingest and health routers, and
    schedules asynchronous PostgreSQL table initialisation on startup so the
    HTTP server is ready to accept requests immediately.

    Returns:
        Configured :class:`FastAPI` application ready to be served by Uvicorn.
    """
    app = FastAPI(
        title='Agentic CI-CD',
        description=(
            'AI agent for RCA traversal based on CI/CD logs. '
            'Connects to Jenkins and GitHub Actions to retrieve, classify '
            'and analyse pipeline failures.'
        ),
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_credentials=True,
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def startup() -> None:
        """Initialise PostgreSQL schema tables asynchronously on first startup."""
        def _init_db() -> None:
            from storage.database import database_obj
            logger.info("Initialising database ...")
            database_obj.init_db()
            database_obj.ensureFailureMetadataTable()
            database_obj.ensureFailurePatternTable()
            logger.info("Database ready.")

        threading.Thread(target=_init_db, daemon=True).start()

    app.include_router(ingest.router, tags=['router'])
    app.include_router(health.router, tags=['health'])

    return app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = create_app()
