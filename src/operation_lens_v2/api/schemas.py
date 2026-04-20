from typing import Literal

from pydantic import BaseModel


class IngestRequest(BaseModel):
    pdf_path: str
    case_ref: str = "UNASSIGNED"
    case_name: str | None = None
    force: bool = False


class EmailThreadIngestRequest(BaseModel):
    parquet_path: str
    case_ref: str = "UNASSIGNED"
    case_name: str | None = None
    force: bool = False


class QueryRequest(BaseModel):
    query: str
    case_ref: str | None = None
    use_cloud: bool | None = None
    chat_history: list[dict[str, str]] | None = None
    recall_mode: str | None = None
    # Investigator-agent scope. Defaults to corpus so the narrative investigator path
    # is used unless a caller explicitly overrides it.
    scope: Literal["document", "case", "corpus"] = "corpus"
    doc_id: str | None = None
    case_scope: str | None = None


class CreateCaseRequest(BaseModel):
    case_ref: str
    case_name: str
