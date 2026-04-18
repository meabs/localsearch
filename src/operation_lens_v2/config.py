from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="config/.env", env_file_encoding="utf-8")

    # ── Application ──────────────────────────────────────────────────────────
    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── Storage paths ─────────────────────────────────────────────────────────
    duckdb_path: str = Field(default="data/evidence.duckdb", alias="DUCKDB_PATH")
    lancedb_path: str = Field(default="data/lancedb", alias="LANCEDB_PATH")
    pdf_root: str = Field(default="data/pdfs", alias="PDF_ROOT")
    evidence_root: str = Field(default="data/pdfs", alias="EVIDENCE_ROOT")
    export_root: str = Field(default="data/exports", alias="EXPORT_ROOT")

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_timeout: float = Field(default=120.0, alias="OLLAMA_TIMEOUT")

    # On-device models (must be present in `ollama list`)
    local_reasoning_model: str = Field(
        default="qwen3.5:32b-instruct-q4_K_M", alias="LOCAL_REASONING_MODEL"
    )
    local_extraction_model: str = Field(
        default="qwen3:8b-instruct-q4_K_M", alias="LOCAL_EXTRACTION_MODEL"
    )
    local_embed_model: str = Field(default="nomic-embed-text", alias="LOCAL_EMBED_MODEL")

    # ── Embeddings ────────────────────────────────────────────────────────────
    embed_dim: int = Field(default=768, alias="EMBED_DIM")

    # ── GLiNER ────────────────────────────────────────────────────────────────
    gliner_model: str = Field(default="urchade/gliner_large-v2.1", alias="GLINER_MODEL")
    gliner_threshold: float = Field(default=0.40, alias="GLINER_THRESHOLD")

    # ── Chunking ──────────────────────────────────────────────────────────────
    chunk_target_tokens: int = Field(default=350, alias="CHUNK_TARGET_TOKENS")
    chunk_max_tokens: int = Field(default=400, alias="CHUNK_MAX_TOKENS")
    chunk_overlap_tokens: int = Field(default=70, alias="CHUNK_OVERLAP_TOKENS")
    chunk_min_tokens: int = Field(default=50, alias="CHUNK_MIN_TOKENS")

    # ── Normalisation ─────────────────────────────────────────────────────────
    alias_threshold: float = Field(default=0.88, alias="ALIAS_THRESHOLD")

    # ── Relationship extraction ───────────────────────────────────────────────
    pattern_confidence: float = Field(default=0.85, alias="PATTERN_CONFIDENCE")
    llm_confidence_min: float = Field(default=0.10, alias="LLM_CONFIDENCE_MIN")
    llm_confidence_max: float = Field(default=0.95, alias="LLM_CONFIDENCE_MAX")
    cooccurrence_confidence: float = Field(default=0.35, alias="COOCCURRENCE_CONFIDENCE")

    # ── Retrieval ─────────────────────────────────────────────────────────────
    vector_top_k: int = Field(default=10, alias="VECTOR_TOP_K")
    fts_top_k: int = Field(default=15, alias="FTS_TOP_K")
    graph_max_hops: int = Field(default=2, alias="GRAPH_MAX_HOPS")
    rerank_top_n: int = Field(default=20, alias="RERANK_TOP_N")
    max_evidence_tokens: int = Field(default=24000, alias="MAX_EVIDENCE_TOKENS")
    hybrid_recall_default: bool = Field(default=True, alias="HYBRID_RECALL_DEFAULT")
    hybrid_candidate_multiplier: int = Field(default=3, alias="HYBRID_CANDIDATE_MULTIPLIER")
    min_doc_coverage: int = Field(default=4, alias="MIN_DOC_COVERAGE")

    # ── Geocoding (Nominatim — sends location strings to OSM) ─────────────────
    geocoding_enabled: bool = Field(default=True, alias="GEOCODING_ENABLED")
    nominatim_base_url: str = Field(
        default="https://nominatim.openstreetmap.org", alias="NOMINATIM_BASE_URL"
    )
    nominatim_user_agent: str = Field(
        default="operation-lens-v2 (local investigator tool)",
        alias="NOMINATIM_USER_AGENT",
    )
    nominatim_country_bias: str = Field(default="gb", alias="NOMINATIM_COUNTRY_BIAS")
    nominatim_min_interval: float = Field(default=1.1, alias="NOMINATIM_MIN_INTERVAL")

    # ── Cloud / OpenRouter (disabled by default) ──────────────────────────────
    allow_cloud_reasoning: bool = Field(default=False, alias="ALLOW_CLOUD_REASONING")
    prefer_openrouter_output: bool = Field(default=False, alias="PREFER_OPENROUTER_OUTPUT")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_model: str = Field(default="anthropic/claude-sonnet-4-5", alias="OPENROUTER_MODEL")

    # ── Derived paths ──────────────────────────────────────────────────────────
    @property
    def duckdb_path_obj(self) -> Path:
        return Path(self.duckdb_path)

    @property
    def lancedb_path_obj(self) -> Path:
        return Path(self.lancedb_path)

    @property
    def pdf_root_obj(self) -> Path:
        return Path(self.pdf_root)

    @property
    def evidence_root_obj(self) -> Path:
        return Path(self.evidence_root)

    @property
    def export_root_obj(self) -> Path:
        return Path(self.export_root)


settings = Settings()
