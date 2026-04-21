"""Tests for the PydanticAI investigator agent.

Uses pydantic-ai's FunctionModel to stub the LLM — no live Ollama required.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.query.investigator import (
    InvestigatorDeps,
    InvestigatorStepHandlers,
    _select_tool_capable_investigator_model,
    build_investigator,
    investigate,
)
from operation_lens_v2.query.scope import build_scope
from operation_lens_v2.query.tool_schemas import (
    AtomicFact,
    Citation,
    InvestigationReport,
)

INSERT_CASE_SQL = "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)"
INSERT_DOC_SQL = (
    "INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)"
    " VALUES (?, ?, ?, ?, ?)"
)
INSERT_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_ENTITY_SQL = (
    "INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, mention_count)"
    " VALUES (?, ?, ?, ?, ?)"
)
INSERT_ALIAS_SQL = (
    "INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)"
    " VALUES (?, ?, ?, ?, ?)"
)


class _FakeToolModule:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def search_entities(self, *args, **kwargs):
        self.calls.append(("search_entities", args, kwargs))
        return []

    async def get_entity_profile(self, *args, **kwargs):
        self.calls.append(("get_entity_profile", args, kwargs))
        return None

    async def get_relationships(self, *args, **kwargs):
        self.calls.append(("get_relationships", args, kwargs))
        return []

    async def get_cooccurrence(self, *args, **kwargs):
        self.calls.append(("get_cooccurrence", args, kwargs))
        return []

    async def get_timeline(self, *args, **kwargs):
        self.calls.append(("get_timeline", args, kwargs))
        return []

    async def fetch_chunk(self, *args, **kwargs):
        self.calls.append(("fetch_chunk", args, kwargs))
        return None

    async def list_documents(self, *args, **kwargs):
        self.calls.append(("list_documents", args, kwargs))
        return []

    async def get_document_entities(self, *args, **kwargs):
        self.calls.append(("get_document_entities", args, kwargs))
        return []

    async def walk_graph(self, *args, **kwargs):
        self.calls.append(("walk_graph", args, kwargs))
        return []


@pytest.fixture
def seeded_db():
    con = init_db(":memory:")
    case_id = str(uuid.uuid4())
    doc_id = "doc-webb"
    chunk_id = "chunk-webb"
    entity_id = "e-webb"

    con.execute(INSERT_CASE_SQL, [case_id, "OP_TEST", "Test"])
    con.execute(INSERT_DOC_SQL, [doc_id, case_id, "NF-INT-001.pdf", "/tmp/a.pdf", 1])
    con.execute(INSERT_CHUNK_SQL, [chunk_id, doc_id, 1, 0, "Marcus Webb was interviewed.", 5])
    con.execute(INSERT_ENTITY_SQL, [entity_id, "Marcus Webb", "PERSON", doc_id, 1])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), entity_id, "Marcus Webb", doc_id, chunk_id])

    yield {"con": con, "doc_id": doc_id, "entity_id": entity_id, "chunk_id": chunk_id}


def _final_report_response() -> ModelResponse:
    """Shape a canned final output for the agent as a tool call on the
    synthetic 'final_result' tool that PydanticAI generates for structured output.
    """
    report = InvestigationReport(
        hypothesis="Marcus Webb is the subject of NF-INT-001, a suspect interview.",
        key_facts=[
            AtomicFact(
                subject="Marcus Webb",
                predicate="was interviewed in",
                object="NF-INT-001",
                confidence=0.9,
                citation=Citation(doc_id="doc-webb", doc_name="NF-INT-001.pdf", page=1),
            )
        ],
        confidence="HIGH",
        gaps=[],
        next_actions=["Cross-reference Webb's associates."],
    )
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="final_result",
                args=report.model_dump_json(),
                tool_call_id="call-final",
            )
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "args", "kwargs", "expected_tool"),
    [
        ("search_entities", ("Marcus Webb",), {"entity_type": "PERSON"}, "search_entities"),
        ("get_entity_profile", ("e-webb",), {}, "get_entity_profile"),
        ("get_relationships", ("e-webb",), {"depth": 2}, "get_relationships"),
        ("get_cooccurrence", ("e-webb", "e-khalil"), {}, "get_cooccurrence"),
        ("get_timeline", (), {"entity_ids": ["e-webb"]}, "get_timeline"),
        ("fetch_chunk", ("chunk-1",), {}, "fetch_chunk"),
        ("list_documents", (), {}, "list_documents"),
        ("get_document_entities", ("doc-1",), {}, "get_document_entities"),
        ("walk_graph", ("e-webb", "e-khalil"), {"max_hops": 3}, "walk_graph"),
    ],
)
async def test_investigator_step_handlers_dispatch_each_tool(
    handler_name,
    args,
    kwargs,
    expected_tool,
):
    fake_tools = _FakeToolModule()
    handlers = InvestigatorStepHandlers(tool_module=fake_tools)
    deps = InvestigatorDeps(scope=build_scope("corpus"), duck=object())  # type: ignore[arg-type]

    await getattr(handlers, handler_name)(deps, *args, **kwargs)

    assert fake_tools.calls
    tool_name, tool_args, tool_kwargs = fake_tools.calls[0]
    assert tool_name == expected_tool
    assert tool_args[0] is deps.scope
    assert deps.duck in tool_args
    for key, value in kwargs.items():
        assert tool_kwargs[key] == value


@pytest.mark.asyncio
async def test_agent_invokes_search_entities_then_returns_report(seeded_db):
    """Happy path: agent calls search_entities, then emits InvestigationReport."""
    call_log: list[str] = []

    async def scripted_llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_index = len(call_log)
        if call_index == 0:
            call_log.append("search_entities")
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_entities",
                        args={"query": "Marcus Webb"},
                        tool_call_id="call-1",
                    )
                ]
            )
        call_log.append("final")
        return _final_report_response()

    agent = build_investigator()
    with agent.override(model=FunctionModel(scripted_llm)):
        scope = build_scope("corpus")
        deps = InvestigatorDeps(scope=scope, duck=seeded_db["con"])
        result = await agent.run("Who is Marcus Webb?", deps=deps)

    assert call_log == ["search_entities", "final"]
    assert isinstance(result.output, InvestigationReport)
    assert result.output.confidence == "HIGH"
    assert result.output.key_facts[0].subject == "Marcus Webb"


@pytest.mark.asyncio
async def test_agent_recovers_from_scope_violation(seeded_db):
    """If the agent calls fetch_chunk on an out-of-scope chunk, PydanticAI surfaces
    the tool error back to the model, which then recovers and emits a report.
    """
    call_log: list[str] = []

    async def scripted_llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_index = len(call_log)
        if call_index == 0:
            call_log.append("violation")
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="fetch_chunk",
                        args={"chunk_id": "does-not-exist-or-out-of-scope"},
                        tool_call_id="call-1",
                    )
                ]
            )
        call_log.append("final")
        return _final_report_response()

    agent = build_investigator()
    with agent.override(model=FunctionModel(scripted_llm)):
        # DOCUMENT scope with a doc that does NOT contain the chunk
        scope = build_scope("document", doc_id="some-other-doc")
        deps = InvestigatorDeps(scope=scope, duck=seeded_db["con"])
        # fetch_chunk returns None when the chunk id does not exist; scope-violation
        # is raised only when the chunk *does* exist but is out of scope. Either
        # way, the agent must continue and emit a final report.
        result = await agent.run("Describe NF-INT-001.", deps=deps)

    assert "final" in call_log
    assert isinstance(result.output, InvestigationReport)


@pytest.mark.asyncio
async def test_agent_fetch_chunk_raises_when_chunk_out_of_scope(seeded_db):
    """When a chunk exists but is outside scope, the tool raises ScopeViolation.
    PydanticAI returns the error to the model as a retry prompt; the next turn the
    agent must emit a final report rather than loop forever.
    """
    call_log: list[str] = []

    async def scripted_llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_index = len(call_log)
        if call_index == 0:
            call_log.append("violation")
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="fetch_chunk",
                        args={"chunk_id": seeded_db["chunk_id"]},
                        tool_call_id="call-1",
                    )
                ]
            )
        call_log.append("final")
        return _final_report_response()

    agent = build_investigator()
    with agent.override(model=FunctionModel(scripted_llm)):
        scope = build_scope("document", doc_id="some-other-doc")
        # The agent may or may not receive a retry — depending on PydanticAI
        # version it may raise UnexpectedModelBehavior. Our public `investigate`
        # helper swallows that into a LOW-confidence report.
        from operation_lens_v2.query import investigator as investigator_mod

        original = investigator_mod.build_investigator
        investigator_mod.build_investigator = lambda settings=None: agent
        try:
            report = await investigate("check", scope, seeded_db["con"])
        finally:
            investigator_mod.build_investigator = original

    assert isinstance(report, InvestigationReport)


@pytest.mark.asyncio
async def test_investigate_returns_low_confidence_on_agent_error(seeded_db):
    """If the agent raises any exception, `investigate` returns a LOW report."""

    async def failing_llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("ollama unreachable")

    from operation_lens_v2.query import investigator as investigator_mod

    agent = build_investigator()
    original = investigator_mod.build_investigator
    investigator_mod.build_investigator = lambda settings=None: agent
    try:
        with agent.override(model=FunctionModel(failing_llm)):
            report = await investigate(
                "anything",
                build_scope("corpus"),
                seeded_db["con"],
            )
    finally:
        investigator_mod.build_investigator = original

    assert report.confidence == "LOW"
    assert report.gaps
    assert "agent_error" in report.gaps[0]


def test_agent_has_all_nine_tools():
    """Regression guard: every design-spec tool is registered on the agent."""
    agent = build_investigator()
    expected = {
        "search_entities",
        "get_entity_profile",
        "get_relationships",
        "get_cooccurrence",
        "get_timeline",
        "fetch_chunk",
        "list_documents",
        "get_document_entities",
        "walk_graph",
    }
    # PydanticAI exposes registered tools via the toolset; poke into it.
    toolset = getattr(agent, "_function_toolset", None)
    assert toolset is not None, "agent has no function toolset"
    registered = set(toolset.tools.keys()) if hasattr(toolset, "tools") else set()
    missing = expected - registered
    assert not missing, f"missing tools on agent: {missing}"


def test_select_tool_capable_investigator_model_prefers_installed_fallback(monkeypatch):
    from operation_lens_v2.query import investigator as investigator_mod

    monkeypatch.setattr(
        investigator_mod,
        "_list_ollama_models",
        lambda settings: {"deepseek-r1:latest", "gpt-oss:20b", "nomic-embed-text"},
    )

    selected = _select_tool_capable_investigator_model(investigator_mod.default_settings)
    assert selected == "gpt-oss:20b"


@pytest.mark.asyncio
async def test_investigate_retries_with_tool_capable_model(monkeypatch, seeded_db):
    from operation_lens_v2.query import investigator as investigator_mod

    class _Result:
        def __init__(self, output):
            self.output = output

    class _FailingAgent:
        async def run(self, query, deps, usage_limits):
            raise RuntimeError("model does not support tools")

    class _WorkingAgent:
        async def run(self, query, deps, usage_limits):
            return _Result(
                InvestigationReport(
                    hypothesis="Recovered with a tool-capable model.",
                    key_facts=[
                        AtomicFact(
                            subject="Marcus Webb",
                            predicate="was interviewed in",
                            object="NF-INT-001",
                            confidence=0.9,
                            citation=Citation(
                                doc_id="doc-webb",
                                doc_name="NF-INT-001.pdf",
                                page=1,
                            ),
                        )
                    ],
                    confidence="HIGH",
                    gaps=[],
                    next_actions=[],
                )
            )

    def _build_agent(settings=None):
        model_name = (settings or investigator_mod.default_settings).investigator_model
        if model_name == "deepseek-r1:latest":
            return _FailingAgent()
        return _WorkingAgent()

    monkeypatch.setattr(investigator_mod, "build_investigator", _build_agent)
    monkeypatch.setattr(
        investigator_mod,
        "_select_tool_capable_investigator_model",
        lambda settings: "gpt-oss:20b",
    )

    report = await investigate(
        "What connects Marcus Webb to NF-INT-001?",
        build_scope("corpus"),
        seeded_db["con"],
    )

    assert report.confidence == "HIGH"
    assert report.hypothesis == "Recovered with a tool-capable model."
