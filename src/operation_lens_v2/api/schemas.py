from pydantic import BaseModel


class IngestRequest(BaseModel):
    pdf_path: str
    case_ref: str = "UNASSIGNED"
    case_name: str | None = None
    force: bool = False


class QueryRequest(BaseModel):
    query: str
    case_ref: str | None = None
    use_cloud: bool | None = None
    chat_history: list[dict[str, str]] | None = None
    recall_mode: str | None = None


class CreateCaseRequest(BaseModel):
    case_ref: str
    case_name: str
    domain_pack: str = "base"


class CaseDomainPackRequest(BaseModel):
    domain_pack: str
    schema_overrides: dict[str, object] | None = None
