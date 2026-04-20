from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from operation_lens_v2.ingestion.duck_store import connect
from operation_lens_v2.ingestion.vector_store import VectorStore

logger = logging.getLogger(__name__)

_duck_connections: dict[str, Any] = {}
_vector_stores: dict[str, VectorStore] = {}
_http_clients: dict[tuple[str, float], httpx.AsyncClient] = {}


def get_duck_connection(path: str):
    """Return a thread-safe DuckDB cursor over the cached base connection.

    DuckDB's ``DuckDBPyConnection`` is not safe to share across threads, but
    FastAPI executes sync endpoints on a worker threadpool. Calling
    ``base.cursor()`` yields a sibling connection over the same database that
    each request/thread can use independently, which avoids the
    "pending query result" races seen under concurrent requests.
    """
    resolved = str(Path(path))
    base = _duck_connections.get(resolved)
    if base is None:
        base = connect(resolved)
        _duck_connections[resolved] = base
    return base.cursor()


def reset_duck_connection(path: str) -> None:
    resolved = str(Path(path))
    base = _duck_connections.pop(resolved, None)
    if base is None:
        return
    try:
        base.close()
    except Exception:
        logger.debug("DuckDB connection close failed during reset", exc_info=True)


def get_vector_store(path: str) -> VectorStore:
    resolved = str(Path(path))
    store = _vector_stores.get(resolved)
    if store is None:
        store = VectorStore(resolved)
        _vector_stores[resolved] = store
    return store


def get_http_client(*, base_url: str, timeout: float) -> httpx.AsyncClient:
    key = (base_url.rstrip("/"), timeout)
    client = _http_clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(base_url=key[0], timeout=timeout)
        _http_clients[key] = client
    return client


async def close_runtime_resources() -> None:
    clients = list(_http_clients.values())
    _http_clients.clear()
    for client in clients:
        if not client.is_closed:
            try:
                await client.aclose()
            except RuntimeError as exc:
                # Windows/asyncio can raise this during teardown when the test loop
                # is already closed. Best effort close is sufficient at this stage.
                if "Event loop is closed" in str(exc):
                    logger.debug("HTTP client close skipped: event loop already closed")
                else:
                    raise

    connections = list(_duck_connections.values())
    _duck_connections.clear()
    for base in connections:
        try:
            base.close()
        except Exception:
            logger.debug("DuckDB connection close failed during shutdown", exc_info=True)

    _vector_stores.clear()


class StageTimer:
    def __init__(self, *, label: str, sink: Callable[[str, float], None] | None = None) -> None:
        self.label = label
        self.sink = sink
        self.timings: dict[str, float] = {}

    @contextmanager
    def measure(self, stage: str):
        start = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - start) * 1000
            self.timings[stage] = elapsed_ms
            if self.sink is not None:
                self.sink(stage, elapsed_ms)

    async def await_stage(self, stage: str, awaitable):
        start = perf_counter()
        try:
            return await awaitable
        finally:
            elapsed_ms = (perf_counter() - start) * 1000
            self.timings[stage] = elapsed_ms
            if self.sink is not None:
                self.sink(stage, elapsed_ms)

    def log_summary(self) -> None:
        if not logger.isEnabledFor(logging.DEBUG) or not self.timings:
            return
        summary = ", ".join(f"{stage}={elapsed:.1f}ms" for stage, elapsed in self.timings.items())
        logger.debug("%s timings: %s", self.label, summary)


def gather_limited(
    coroutines: list[Any],
    *,
    limit: int,
):
    semaphore = asyncio.Semaphore(max(1, limit))

    async def runner(coro):
        async with semaphore:
            return await coro

    return asyncio.gather(*(runner(coro) for coro in coroutines))
