from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ParsedTextBlock:
    text: str
    page: int
    source_label: str
    provenance_type: str = "native_text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedAttachment:
    filename: str
    mime_type: str
    file_size: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DerivedFact:
    fact_type: str
    fact_value: str
    provenance_type: str
    source_label: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParserWarning:
    warning_code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedDocument:
    document_id: str
    source_type: str
    source_metadata: dict[str, Any]
    text_blocks: list[ParsedTextBlock]
    attachments: list[ParsedAttachment] = field(default_factory=list)
    derived_facts: list[DerivedFact] = field(default_factory=list)
    parser_warnings: list[ParserWarning] = field(default_factory=list)
    parser_name: str = "unknown"
    perceptual_hash: str | None = None


class BaseParser(ABC):
    parser_name = "base"
    supported_extensions: tuple[str, ...] = ()
    supported_mime_prefixes: tuple[str, ...] = ()

    def can_parse(self, path: Path, mime_type: str | None = None) -> bool:
        suffix = path.suffix.lower()
        guessed = (mime_type or mimetypes.guess_type(path.name)[0] or "").lower()
        if suffix in self.supported_extensions:
            return True
        return any(guessed.startswith(prefix) for prefix in self.supported_mime_prefixes)

    @abstractmethod
    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        raise NotImplementedError
