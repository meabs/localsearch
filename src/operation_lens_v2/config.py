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

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_timeout: float = Field(default=120.0, alias="OLLAMA_TIMEOUT")

    # On-device models (must be present in `ollama list`)
    local_reasoning_model: str = Field(
        default="deepseek-r1:latest", alias="LOCAL_REASONING_MODEL"
    )
    local_extraction_model: str = Field(
        default="llama3.1:8b-instruct-q4_K_M", alias="LOCAL_EXTRACTION_MODEL"
    )
    local_embed_model: str = Field(default="nomic-embed-text", alias="LOCAL_EMBED_MODEL")

    # Investigator agent models (tool-calling loop + briefing writer + critic)
    investigator_model: str = Field(default="gpt-oss:20b", alias="INVESTIGATOR_MODEL")
    writer_model: str = Field(default="deepseek-r1:latest", alias="WRITER_MODEL")
    critic_model: str = Field(default="llama3.1:8b-instruct-q4_K_M", alias="CRITIC_MODEL")
    investigator_max_iterations: int = Field(default=25, alias="INVESTIGATOR_MAX_ITERATIONS")
    investigator_timeout: float = Field(default=180.0, alias="INVESTIGATOR_TIMEOUT")

    # ── Embeddings ────────────────────────────────────────────────────────────
    embed_dim: int = Field(default=768, alias="EMBED_DIM")
    embed_batch_size: int = Field(default=32, alias="EMBED_BATCH_SIZE")
    embed_retry_attempts: int = Field(default=3, alias="EMBED_RETRY_ATTEMPTS")
    embed_retry_backoff: float = Field(default=2.0, alias="EMBED_RETRY_BACKOFF")

    # ── LLM router limits ─────────────────────────────────────────────────────
    openrouter_citation_limit: int = Field(default=3, alias="OPENROUTER_CITATION_LIMIT")
    openrouter_findings_limit: int = Field(default=10, alias="OPENROUTER_FINDINGS_LIMIT")
    local_findings_limit: int = Field(default=8, alias="LOCAL_FINDINGS_LIMIT")
    relationship_findings_limit: int = Field(default=8, alias="RELATIONSHIP_FINDINGS_LIMIT")
    exact_findings_limit: int = Field(default=10, alias="EXACT_FINDINGS_LIMIT")
    chunk_findings_limit: int = Field(default=6, alias="CHUNK_FINDINGS_LIMIT")

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

    # ── Entity review ─────────────────────────────────────────────────────────
    # Entities whose stored confidence falls below this threshold are surfaced
    # as "candidates" in the Audit view so a human can confirm or discard them.
    low_confidence_threshold: float = Field(
        default=0.50, alias="LOW_CONFIDENCE_THRESHOLD"
    )
    ocr_low_confidence_threshold: float = Field(
        default=0.60, alias="OCR_LOW_CONFIDENCE_THRESHOLD"
    )

    # Optional local transcription / handwritten OCR models. These are only
    # used when their heavy runtime packages are installed in the local env.
    whisper_model: str = Field(default="base", alias="WHISPER_MODEL")
    ffmpeg_path: str = Field(default="", alias="FFMPEG_PATH")
    media_object_detection_model: str = Field(
        default="data/models/yolov8n.pt", alias="MEDIA_OBJECT_DETECTION_MODEL"
    )
    media_object_frame_count: int = Field(default=8, alias="MEDIA_OBJECT_FRAME_COUNT")
    media_object_confidence: float = Field(default=0.25, alias="MEDIA_OBJECT_CONFIDENCE")
    trocr_model: str = Field(
        default="microsoft/trocr-base-handwritten", alias="TROCR_MODEL"
    )

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
    rerank_rrf_k: int = Field(default=60, alias="RERANK_RRF_K")
    rerank_source_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "graph": 1.3,
            "exact": 1.1,
            "vector": 1.0,
            "fts": 0.9,
            "document": 1.0,
        },
        alias="RERANK_SOURCE_WEIGHTS",
    )
    max_evidence_tokens: int = Field(default=24000, alias="MAX_EVIDENCE_TOKENS")
    hybrid_recall_default: bool = Field(default=True, alias="HYBRID_RECALL_DEFAULT")
    hybrid_candidate_multiplier: int = Field(default=3, alias="HYBRID_CANDIDATE_MULTIPLIER")
    min_doc_coverage: int = Field(default=4, alias="MIN_DOC_COVERAGE")
    query_cache_enabled: bool = Field(default=True, alias="QUERY_CACHE_ENABLED")
    query_cache_max_entries: int = Field(default=128, alias="QUERY_CACHE_MAX_ENTRIES")

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


settings = Settings()
