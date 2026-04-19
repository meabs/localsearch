"""PydanticAI-based investigator agent.

Runs a ReAct loop over the 9 scope-enforced tools in tools.py, targeting the local
Ollama model via its OpenAI-compatible /v1 endpoint. The agent emits a structured
InvestigationReport; the briefing writer composes the final narrative from that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import duckdb

from operation_lens_v2.config import Settings, settings as default_settings
from operation_lens_v2.query import tools
from operation_lens_v2.query.prompts import render_investigator_prompt
from operation_lens_v2.query.scope import ScopeContext, ScopeViolation
from operation_lens_v2.query.tool_schemas import (
    ChunkText,
    CooccurrenceHit,
    DatedEvent,
    DocumentSummary,
    EntityHit,
    EntityProfile,
    GraphPath,
    InvestigationReport,
    RelationshipEdge,
)

logger = logging.getLogger(__name__)

# PydanticAI is imported lazily so the app can still boot when the optional
# dependency is missing. These placeholders keep runtime annotation resolution
# from crashing when the package is available and tool schemas are built.
Agent = Any
RunContext = Any
OpenAIChatModel = Any


@dataclass
class InvestigatorDeps:
    """Per-run dependencies: scope and a DuckDB connection."""

    scope: ScopeContext
    duck: duckdb.DuckDBPyConnection


def _build_model(settings: Settings) -> OpenAIChatModel:
    """Point PydanticAI at the local Ollama /v1 endpoint."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
        api_key="ollama",
    )
    return OpenAIChatModel(settings.investigator_model, provider=provider)


def _scope_guard(tool_name: str):
    """Decorator translating ScopeViolation into ModelRetry so the agent can recover
    without crashing the run. The model sees the error message and typically retries
    with a valid in-scope argument.
    """
    from functools import wraps
    from pydantic_ai import ModelRetry

    def decorator(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except ScopeViolation as exc:
                raise ModelRetry(
                    f"{tool_name} refused: {exc}. Choose a different id that is within the "
                    "investigation scope, or stop and compose the report with the evidence you "
                    "already have."
                ) from exc

        return wrapped

    return decorator


def build_investigator(
    settings: Settings = default_settings,
) -> Agent[InvestigatorDeps, InvestigationReport]:
    """Construct a PydanticAI investigator agent bound to the local Ollama model."""
    try:
        from pydantic_ai import Agent, RunContext
    except ImportError as exc:
        raise RuntimeError("pydantic-ai-slim is not installed in the active environment.") from exc
    globals()["Agent"] = Agent
    globals()["RunContext"] = RunContext

    agent: Agent[InvestigatorDeps, InvestigationReport] = Agent(
        _build_model(settings),
        deps_type=InvestigatorDeps,
        output_type=InvestigationReport,
        retries=2,
    )

    @agent.system_prompt
    def _system_prompt(ctx: RunContext[InvestigatorDeps]) -> str:
        return render_investigator_prompt(ctx.deps.scope)

    @agent.tool
    @_scope_guard("search_entities")
    async def search_entities(
        ctx: RunContext[InvestigatorDeps],
        query: str,
        entity_type: str | None = None,
    ) -> list[EntityHit]:
        """Find entities whose canonical name or aliases match the query."""
        return await tools.search_entities(
            ctx.deps.scope, query, ctx.deps.duck, entity_type=entity_type
        )

    @agent.tool
    @_scope_guard("get_entity_profile")
    async def get_entity_profile(
        ctx: RunContext[InvestigatorDeps],
        entity_id: str,
    ) -> EntityProfile | None:
        """Aggregate profile for one entity within the current scope."""
        return await tools.get_entity_profile(ctx.deps.scope, entity_id, ctx.deps.duck)

    @agent.tool
    @_scope_guard("get_relationships")
    async def get_relationships(
        ctx: RunContext[InvestigatorDeps],
        entity_id: str,
        depth: int = 1,
    ) -> list[RelationshipEdge]:
        """Graph edges touching an entity, up to depth 1 or 2."""
        return await tools.get_relationships(
            ctx.deps.scope, entity_id, ctx.deps.duck, depth=depth
        )

    @agent.tool
    @_scope_guard("get_cooccurrence")
    async def get_cooccurrence(
        ctx: RunContext[InvestigatorDeps],
        entity_a_id: str,
        entity_b_id: str,
    ) -> list[CooccurrenceHit]:
        """Chunks where both entities appear together."""
        return await tools.get_cooccurrence(
            ctx.deps.scope, entity_a_id, entity_b_id, ctx.deps.duck
        )

    @agent.tool
    @_scope_guard("get_timeline")
    async def get_timeline(
        ctx: RunContext[InvestigatorDeps],
        entity_ids: list[str] | None = None,
    ) -> list[DatedEvent]:
        """Dated events in scope, chronologically ordered."""
        return await tools.get_timeline(ctx.deps.scope, ctx.deps.duck, entity_ids=entity_ids)

    @agent.tool
    @_scope_guard("fetch_chunk")
    async def fetch_chunk(
        ctx: RunContext[InvestigatorDeps],
        chunk_id: str,
    ) -> ChunkText | None:
        """Full text of a single chunk, for precise quoting."""
        return await tools.fetch_chunk(ctx.deps.scope, chunk_id, ctx.deps.duck)

    @agent.tool
    @_scope_guard("list_documents")
    async def list_documents(
        ctx: RunContext[InvestigatorDeps],
    ) -> list[DocumentSummary]:
        """Documents reachable in the current scope."""
        return await tools.list_documents(ctx.deps.scope, ctx.deps.duck)

    @agent.tool
    @_scope_guard("get_document_entities")
    async def get_document_entities(
        ctx: RunContext[InvestigatorDeps],
        doc_id: str,
    ) -> list[EntityHit]:
        """All entities mentioned in a single document."""
        return await tools.get_document_entities(ctx.deps.scope, doc_id, ctx.deps.duck)

    @agent.tool
    @_scope_guard("walk_graph")
    async def walk_graph(
        ctx: RunContext[InvestigatorDeps],
        source_entity_id: str,
        target_entity_id: str,
        max_hops: int = 3,
    ) -> list[GraphPath]:
        """Evidence-backed paths between two entities."""
        return await tools.walk_graph(
            ctx.deps.scope,
            source_entity_id,
            target_entity_id,
            ctx.deps.duck,
            max_hops=max_hops,
        )

    return agent


async def investigate(
    query: str,
    scope: ScopeContext,
    duck: duckdb.DuckDBPyConnection,
    *,
    settings: Settings = default_settings,
) -> InvestigationReport:
    """Run the investigator agent and return its structured report.

    On any failure (LLM error, validation error, tool error that exhausts retries),
    returns a low-confidence report rather than raising — the router falls back to
    the deterministic path in that case.
    """
    agent = build_investigator(settings)
    deps = InvestigatorDeps(scope=scope, duck=duck)
    try:
        from pydantic_ai.usage import UsageLimits
    except ImportError as exc:
        raise RuntimeError("pydantic-ai-slim is not installed in the active environment.") from exc

    limits = UsageLimits(request_limit=settings.investigator_max_iterations)
    try:
        result = await agent.run(query, deps=deps, usage_limits=limits)
    except Exception as exc:
        logger.warning("Investigator agent failed: %s", exc)
        return InvestigationReport(
            hypothesis="Investigator agent failed to complete the investigation.",
            confidence="LOW",
            gaps=[f"agent_error: {type(exc).__name__}"],
        )
    return result.output
