"""PydanticAI-based investigator agent.

Runs a ReAct loop over the 9 scope-enforced tools in tools.py, targeting the local
Ollama model via its OpenAI-compatible /v1 endpoint. The agent emits a structured
InvestigationReport; the briefing writer composes the final narrative from that.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import wraps
from typing import Any

import duckdb
import httpx

from operation_lens_v2.config import Settings
from operation_lens_v2.config import settings as default_settings
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
TOOL_CAPABLE_MODEL_CANDIDATES = (
    "gpt-oss:20b",
    "gemma4:26b",
    "glm-4.7-flash:latest",
)


@dataclass
class InvestigatorDeps:
    """Per-run dependencies: scope and a DuckDB connection."""

    scope: ScopeContext
    duck: duckdb.DuckDBPyConnection


@dataclass(slots=True)
class InvestigatorStepHandlers:
    """Tool-call handlers for one investigator ReAct step.

    Keeping these handlers separate from PydanticAI registration makes each tool
    dispatch testable without driving a full model loop.
    """

    tool_module: Any = tools

    async def search_entities(
        self,
        deps: InvestigatorDeps,
        query: str,
        entity_type: str | None = None,
    ) -> list[EntityHit]:
        return await self.tool_module.search_entities(
            deps.scope, query, deps.duck, entity_type=entity_type
        )

    async def get_entity_profile(
        self,
        deps: InvestigatorDeps,
        entity_id: str,
    ) -> EntityProfile | None:
        return await self.tool_module.get_entity_profile(deps.scope, entity_id, deps.duck)

    async def get_relationships(
        self,
        deps: InvestigatorDeps,
        entity_id: str,
        depth: int = 1,
    ) -> list[RelationshipEdge]:
        return await self.tool_module.get_relationships(
            deps.scope, entity_id, deps.duck, depth=depth
        )

    async def get_cooccurrence(
        self,
        deps: InvestigatorDeps,
        entity_a_id: str,
        entity_b_id: str,
    ) -> list[CooccurrenceHit]:
        return await self.tool_module.get_cooccurrence(
            deps.scope, entity_a_id, entity_b_id, deps.duck
        )

    async def get_timeline(
        self,
        deps: InvestigatorDeps,
        entity_ids: list[str] | None = None,
    ) -> list[DatedEvent]:
        return await self.tool_module.get_timeline(
            deps.scope, deps.duck, entity_ids=entity_ids
        )

    async def fetch_chunk(
        self,
        deps: InvestigatorDeps,
        chunk_id: str,
    ) -> ChunkText | None:
        return await self.tool_module.fetch_chunk(deps.scope, chunk_id, deps.duck)

    async def list_documents(
        self,
        deps: InvestigatorDeps,
    ) -> list[DocumentSummary]:
        return await self.tool_module.list_documents(deps.scope, deps.duck)

    async def get_document_entities(
        self,
        deps: InvestigatorDeps,
        doc_id: str,
    ) -> list[EntityHit]:
        return await self.tool_module.get_document_entities(deps.scope, doc_id, deps.duck)

    async def walk_graph(
        self,
        deps: InvestigatorDeps,
        source_entity_id: str,
        target_entity_id: str,
        max_hops: int = 3,
    ) -> list[GraphPath]:
        return await self.tool_module.walk_graph(
            deps.scope,
            source_entity_id,
            target_entity_id,
            deps.duck,
            max_hops=max_hops,
        )


def _build_model(settings: Settings) -> OpenAIChatModel:
    """Point PydanticAI at the local Ollama /v1 endpoint."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
        api_key="ollama",
    )
    return OpenAIChatModel(settings.investigator_model, provider=provider)


def _is_tool_support_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "does not support tools" in message or "support tools" in message


def _list_ollama_models(settings: Settings) -> set[str]:
    """Return installed Ollama model tags, or an empty set if Ollama is unavailable."""
    try:
        with httpx.Client(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=min(settings.ollama_timeout, 15.0),
        ) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Unable to list Ollama models for investigator fallback: %s", exc)
        return set()

    models = payload.get("models", [])
    installed: set[str] = set()
    for model in models:
        name = str(model.get("name") or "").strip()
        if name:
            installed.add(name)
    return installed


def _select_tool_capable_investigator_model(settings: Settings) -> str | None:
    """Pick a local Ollama model that supports tool calling for the investigator."""
    installed = _list_ollama_models(settings)
    if not installed:
        return None
    if (
        settings.investigator_model in installed
        and settings.investigator_model in TOOL_CAPABLE_MODEL_CANDIDATES
    ):
        return settings.investigator_model
    for candidate in TOOL_CAPABLE_MODEL_CANDIDATES:
        if candidate in installed:
            return candidate
    return None


def _scope_guard(tool_name: str):
    """Decorator translating ScopeViolation into ModelRetry so the agent can recover
    without crashing the run. The model sees the error message and typically retries
    with a valid in-scope argument.
    """

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
        retries=5,
    )

    @agent.system_prompt
    def _system_prompt(ctx: RunContext[InvestigatorDeps]) -> str:
        return render_investigator_prompt(ctx.deps.scope)

    register_investigator_tools(agent, InvestigatorStepHandlers())
    return agent


def register_investigator_tools(
    agent: Agent[InvestigatorDeps, InvestigationReport],
    handlers: InvestigatorStepHandlers,
) -> None:
    """Register the scope-guarded investigator tools on an agent."""

    @agent.tool
    @_scope_guard("search_entities")
    async def search_entities(
        ctx: RunContext[InvestigatorDeps],
        query: str,
        entity_type: str | None = None,
    ) -> list[EntityHit]:
        """Find entities whose canonical name or aliases match the query."""
        return await handlers.search_entities(ctx.deps, query, entity_type=entity_type)

    @agent.tool
    @_scope_guard("get_entity_profile")
    async def get_entity_profile(
        ctx: RunContext[InvestigatorDeps],
        entity_id: str,
    ) -> EntityProfile | None:
        """Aggregate profile for one entity within the current scope."""
        return await handlers.get_entity_profile(ctx.deps, entity_id)

    @agent.tool
    @_scope_guard("get_relationships")
    async def get_relationships(
        ctx: RunContext[InvestigatorDeps],
        entity_id: str,
        depth: int = 1,
    ) -> list[RelationshipEdge]:
        """Graph edges touching an entity, up to depth 1 or 2."""
        return await handlers.get_relationships(ctx.deps, entity_id, depth=depth)

    @agent.tool
    @_scope_guard("get_cooccurrence")
    async def get_cooccurrence(
        ctx: RunContext[InvestigatorDeps],
        entity_a_id: str,
        entity_b_id: str,
    ) -> list[CooccurrenceHit]:
        """Chunks where both entities appear together."""
        return await handlers.get_cooccurrence(ctx.deps, entity_a_id, entity_b_id)

    @agent.tool
    @_scope_guard("get_timeline")
    async def get_timeline(
        ctx: RunContext[InvestigatorDeps],
        entity_ids: list[str] | None = None,
    ) -> list[DatedEvent]:
        """Dated events in scope, chronologically ordered."""
        return await handlers.get_timeline(ctx.deps, entity_ids=entity_ids)

    @agent.tool
    @_scope_guard("fetch_chunk")
    async def fetch_chunk(
        ctx: RunContext[InvestigatorDeps],
        chunk_id: str,
    ) -> ChunkText | None:
        """Full text of a single chunk, for precise quoting."""
        return await handlers.fetch_chunk(ctx.deps, chunk_id)

    @agent.tool
    @_scope_guard("list_documents")
    async def list_documents(
        ctx: RunContext[InvestigatorDeps],
    ) -> list[DocumentSummary]:
        """Documents reachable in the current scope."""
        return await handlers.list_documents(ctx.deps)

    @agent.tool
    @_scope_guard("get_document_entities")
    async def get_document_entities(
        ctx: RunContext[InvestigatorDeps],
        doc_id: str,
    ) -> list[EntityHit]:
        """All entities mentioned in a single document."""
        return await handlers.get_document_entities(ctx.deps, doc_id)

    @agent.tool
    @_scope_guard("walk_graph")
    async def walk_graph(
        ctx: RunContext[InvestigatorDeps],
        source_entity_id: str,
        target_entity_id: str,
        max_hops: int = 3,
    ) -> list[GraphPath]:
        """Evidence-backed paths between two entities."""
        return await handlers.walk_graph(
            ctx.deps,
            source_entity_id,
            target_entity_id,
            max_hops=max_hops,
        )


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
    try:
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.usage import UsageLimits
    except ImportError as exc:
        raise RuntimeError("pydantic-ai-slim is not installed in the active environment.") from exc

    agent = build_investigator(settings)
    deps = InvestigatorDeps(scope=scope, duck=duck)
    limits = UsageLimits(request_limit=settings.investigator_max_iterations)
    try:
        result = await agent.run(query, deps=deps, usage_limits=limits)
    except UsageLimitExceeded:
        logger.warning(
            "Investigator exhausted its %d-request budget before finishing",
            settings.investigator_max_iterations,
        )
        return InvestigationReport(
            hypothesis="Investigator ran out of tool-call budget before reaching a conclusion.",
            confidence="LOW",
            gaps=[
                f"iteration_budget_exhausted: hit {settings.investigator_max_iterations}-request "
                "limit; raise INVESTIGATOR_MAX_ITERATIONS or narrow the query scope"
            ],
        )
    except Exception as exc:
        if _is_tool_support_error(exc):
            fallback_model = _select_tool_capable_investigator_model(settings)
            if fallback_model and fallback_model != settings.investigator_model:
                logger.warning(
                    "Investigator model %s does not support tools; retrying with %s",
                    settings.investigator_model,
                    fallback_model,
                )
                fallback_settings = settings.model_copy(
                    update={"investigator_model": fallback_model}
                )
                fallback_agent = build_investigator(fallback_settings)
                try:
                    result = await fallback_agent.run(query, deps=deps, usage_limits=limits)
                    return result.output
                except Exception as fallback_exc:
                    logger.warning(
                        "Investigator fallback model %s also failed: %s",
                        fallback_model,
                        fallback_exc,
                    )
                    exc = fallback_exc
        logger.warning("Investigator agent failed: %s", exc)
        return InvestigationReport(
            hypothesis="Investigator agent failed to complete the investigation.",
            confidence="LOW",
            gaps=[f"agent_error: {type(exc).__name__}"],
        )
    return result.output


def _preview(value: Any, limit: int = 400) -> str:
    """Stringify a tool argument or result for UI preview, truncating cleanly."""
    if value is None:
        return ""
    try:
        if hasattr(value, "model_dump"):
            text = str(value.model_dump())
        elif isinstance(value, (list, tuple)):
            text = f"[{len(value)} items] " + ", ".join(_preview(v, 80) for v in value[:3])
            if len(value) > 3:
                text += f", …(+{len(value) - 3} more)"
        else:
            text = str(value)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


async def investigate_stream(
    query: str,
    scope: ScopeContext,
    duck: duckdb.DuckDBPyConnection,
    *,
    settings: Settings = default_settings,
) -> AsyncIterator[dict[str, Any]]:
    """Run the investigator and yield trace events as they occur.

    Event kinds:
      - ``tool_call``      {tool, args_preview, tool_call_id}
      - ``tool_result``    {tool, result_preview, tool_call_id}
      - ``thought``        {text}            (assistant prose between tool turns)
      - ``thinking``       {text}            (explicit reasoning block, if model emits one)
      - ``report``         {report}          terminal — structured InvestigationReport
      - ``error``          {error_type, message, recoverable}
    """
    try:
        from pydantic_ai._agent_graph import CallToolsNode, End, ModelRequestNode
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.messages import (
            TextPart,
            ThinkingPart,
            ToolCallPart,
            ToolReturnPart,
        )
        from pydantic_ai.usage import UsageLimits
    except ImportError as exc:
        raise RuntimeError("pydantic-ai-slim is not installed in the active environment.") from exc

    agent = build_investigator(settings)
    deps = InvestigatorDeps(scope=scope, duck=duck)
    limits = UsageLimits(request_limit=settings.investigator_max_iterations)

    async def _drive(run_agent) -> AsyncIterator[dict[str, Any]]:
        async with run_agent.iter(query, deps=deps, usage_limits=limits) as agent_run:
            async for node in agent_run:
                if isinstance(node, CallToolsNode):
                    for part in node.model_response.parts:
                        if isinstance(part, ToolCallPart):
                            yield {
                                "kind": "tool_call",
                                "tool": part.tool_name,
                                "args_preview": _preview(part.args),
                                "tool_call_id": part.tool_call_id,
                            }
                        elif isinstance(part, ThinkingPart):
                            if part.content:
                                yield {"kind": "thinking", "text": part.content}
                        elif isinstance(part, TextPart):
                            if part.content and part.content.strip():
                                yield {"kind": "thought", "text": part.content}
                elif isinstance(node, ModelRequestNode):
                    for part in node.request.parts:
                        if isinstance(part, ToolReturnPart):
                            yield {
                                "kind": "tool_result",
                                "tool": part.tool_name,
                                "result_preview": _preview(part.content),
                                "tool_call_id": part.tool_call_id,
                            }
                elif isinstance(node, End):
                    output = getattr(node.data, "output", None)
                    if output is not None and hasattr(output, "model_dump"):
                        yield {"kind": "report", "report": output.model_dump()}

    try:
        async for event in _drive(agent):
            yield event
    except UsageLimitExceeded:
        logger.warning(
            "Investigator exhausted its %d-request budget before finishing",
            settings.investigator_max_iterations,
        )
        yield {
            "kind": "error",
            "error_type": "UsageLimitExceeded",
            "message": (
                f"Hit {settings.investigator_max_iterations}-request budget. "
                "Raise INVESTIGATOR_MAX_ITERATIONS or narrow the query scope."
            ),
            "recoverable": False,
        }
        yield {
            "kind": "report",
            "report": InvestigationReport(
                hypothesis="Investigator ran out of tool-call budget before reaching a conclusion.",
                confidence="LOW",
                gaps=[
                    "iteration_budget_exhausted: hit "
                    f"{settings.investigator_max_iterations}-request limit"
                ],
            ).model_dump(),
        }
    except Exception as exc:
        if _is_tool_support_error(exc):
            fallback_model = _select_tool_capable_investigator_model(settings)
            if fallback_model and fallback_model != settings.investigator_model:
                logger.warning(
                    "Investigator model %s does not support tools; retrying stream with %s",
                    settings.investigator_model,
                    fallback_model,
                )
                yield {
                    "kind": "error",
                    "error_type": "ToolSupportError",
                    "message": (
                        f"{settings.investigator_model} lacks tool support; "
                        f"retrying with {fallback_model}"
                    ),
                    "recoverable": True,
                }
                fallback_settings = settings.model_copy(
                    update={"investigator_model": fallback_model}
                )
                fallback_agent = build_investigator(fallback_settings)
                try:
                    async for event in _drive(fallback_agent):
                        yield event
                    return
                except Exception as fallback_exc:
                    exc = fallback_exc
        logger.warning("Investigator stream failed: %s", exc)
        yield {
            "kind": "error",
            "error_type": type(exc).__name__,
            "message": str(exc)[:400],
            "recoverable": False,
        }
        yield {
            "kind": "report",
            "report": InvestigationReport(
                hypothesis="Investigator agent failed to complete the investigation.",
                confidence="LOW",
                gaps=[f"agent_error: {type(exc).__name__}"],
            ).model_dump(),
        }
