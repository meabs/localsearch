from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from operation_lens_v2.api.routes import audit, cases, documents, graph, ingest, query, timeline
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.logging_utils import setup_logging
from operation_lens_v2.runtime import close_runtime_resources, get_duck_connection

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Ensure schema exists, then pin a shared runtime connection for request handlers.
    init_con = duck_store.init_db(settings.duckdb_path)
    init_con.close()
    get_duck_connection(settings.duckdb_path)
    yield
    await close_runtime_resources()


app = FastAPI(title="Operation Lens v2", lifespan=lifespan)
app.include_router(ingest.router)
app.include_router(query.router)
app.include_router(graph.router)
app.include_router(audit.router)
app.include_router(cases.router)
app.include_router(timeline.router)
app.include_router(documents.router)
app.mount(
    "/ui",
    StaticFiles(directory="src/operation_lens_v2/frontend", html=True),
    name="ui",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
