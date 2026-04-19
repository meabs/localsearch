from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from operation_lens_v2.api.routes import audit, cases, entities, graph, ingest, query, timeline
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.logging_utils import setup_logging
from operation_lens_v2.runtime import close_runtime_resources

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Apply any pending schema migrations (idempotent ALTER TABLEs).
    con = duck_store.connect(settings.duckdb_path)
    try:
        duck_store._ensure_case_columns(con)  # noqa: SLF001
        duck_store._ensure_geocode_columns(con)  # noqa: SLF001
        duck_store._ensure_attachments_table(con)  # noqa: SLF001
        duck_store._ensure_temporal_columns(con)  # noqa: SLF001
        duck_store._ensure_graph_indexes(con)  # noqa: SLF001
    finally:
        con.close()
    yield
    await close_runtime_resources()


app = FastAPI(title="Operation Lens v2", lifespan=lifespan)
app.include_router(ingest.router)
app.include_router(query.router)
app.include_router(graph.router)
app.include_router(audit.router)
app.include_router(cases.router)
app.include_router(entities.router)
app.include_router(timeline.router)
app.mount(
    "/ui",
    StaticFiles(directory="src/operation_lens_v2/frontend", html=True),
    name="ui",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
