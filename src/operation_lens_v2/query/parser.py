from __future__ import annotations

import re

INVENTORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LOCATION", ("location", "place")),
    ("EMAIL", ("email", "e-mail")),
    ("WEAPON", ("weapon", "gun", "knife", "firearm")),
    ("DRUG", ("drug", "narcotic")),
    ("BANK_ACCOUNT", ("bank", "iban", "credit card")),
    ("SERIAL", ("serial", "imei")),
    ("PHONE", ("phone", "telephone", "mobile", "cell")),
    ("VEHICLE", ("vehicle", "car", "plate")),
    ("ORGANISATION", ("organisation", "organization", "company")),
    ("PERSON", ("person", "people")),
)
LIST_QUERY_TOKENS = ("list", "all", "key", "show me", "what are")
RELATIONSHIP_HINTS = (
    "associated with",
    "connected to",
    "linked to",
    "known associates",
    "associates of",
    "relationship",
    "relationships",
    "network",
)
HIGH_RECALL_HINTS = (
    "comprehensive",
    "exhaustive",
    "full coverage",
    "don't miss",
    "dont miss",
    "all relevant",
    "every related",
)
PERSON_NAME_PATTERN = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
SINGLE_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9._-]+\b")
CONNECTION_HINT_PATTERN = re.compile(
    r"\b(connect|connects|connected|link|links|linked|between|relationship|relate|relates|related)\b"
)
HOW_RELATE_PATTERN = re.compile(r"\bhow\s+does\b.*\brelate\b")
CONNECTION_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "between",
    "can",
    "connect",
    "connected",
    "connects",
    "does",
    "for",
    "from",
    "give",
    "how",
    "is",
    "link",
    "linked",
    "links",
    "me",
    "of",
    "on",
    "or",
    "related",
    "relate",
    "relates",
    "relationship",
    "the",
    "to",
    "what",
    "which",
    "who",
    "why",
}
ADDRESS_LIKE_SUFFIXES = {
    "road",
    "street",
    "lane",
    "avenue",
    "close",
    "court",
    "drive",
    "yard",
    "dock",
    "estate",
    "gardens",
    "garden",
    "house",
    "park",
    "terrace",
    "wharf",
    "marina",
    "pier",
    "block",
    "units",
}
EXACT_TOKEN_PATTERN = re.compile(
    r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b"
    r"|\+?\d[\d\s]{8,}\d"
    r"|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
DOCUMENT_REFERENCE_PATTERN = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]*\.pdf\b", re.IGNORECASE)
DOCUMENT_SUMMARY_TOKENS = (
    "summarise",
    "summarize",
    "summary",
    "brief",
    "key findings",
    "findings",
    "overview",
)


def _contains_any_token(query_lower: str, tokens: tuple[str, ...]) -> bool:
    return any(token in query_lower for token in tokens)


def _inventory_target_for_query(query_lower: str) -> str | None:
    if re.search(r"\bip\b", query_lower) or _contains_any_token(query_lower, ("ipv4", "ipv6")):
        return "IP_ADDRESS"
    for entity_type, keywords in INVENTORY_KEYWORDS:
        if _contains_any_token(query_lower, keywords):
            return entity_type
    return None


def _looks_like_address_phrase(value: str) -> bool:
    tokens = [token.strip(".,") for token in value.split() if token.strip(".,")]
    if len(tokens) < 2:
        return False
    return tokens[-1].lower() in ADDRESS_LIKE_SUFFIXES


def _collect_connection_entities(query: str) -> list[str]:
    candidates: list[str] = []
    excluded_single_tokens: set[str] = set()
    for hit in PERSON_NAME_PATTERN.findall(query):
        if not _looks_like_address_phrase(hit):
            candidates.append(hit)
            excluded_single_tokens.update(part.lower() for part in hit.split())
    for hit in EXACT_TOKEN_PATTERN.findall(query):
        candidates.append(hit)
    for hit in SINGLE_ENTITY_PATTERN.findall(query):
        lowered = hit.lower()
        if lowered in CONNECTION_QUERY_STOPWORDS:
            continue
        if lowered in excluded_single_tokens:
            continue
        if _looks_like_address_phrase(hit):
            continue
        candidates.append(hit)
    return list(dict.fromkeys(candidates))


def _is_connection_query(query_lower: str, entity_mentions: list[str]) -> bool:
    return (
        len(entity_mentions) >= 2
        and (
            _contains_any_token(query_lower, ("connect", "link", "between", "relationship"))
            or bool(HOW_RELATE_PATTERN.search(query_lower))
        )
        and bool(CONNECTION_HINT_PATTERN.search(query_lower) or HOW_RELATE_PATTERN.search(query_lower))
    )


def parse_query(query: str) -> dict[str, object]:
    entities: list[str] = []
    document_refs: list[str] = []
    intent = "general_query"
    inventory_target: str | None = None
    q = query.lower()

    person_hits = [
        hit for hit in PERSON_NAME_PATTERN.findall(query) if not _looks_like_address_phrase(hit)
    ]
    if person_hits:
        entities.extend(person_hits)
        intent = "entity_relationship_query"

    exact_tokens = EXACT_TOKEN_PATTERN.findall(query)
    if exact_tokens:
        intent = "exact_identifier_query"

    connection_entities = _collect_connection_entities(query)
    resolved_entity_ids: list[str] = []
    if _is_connection_query(q, connection_entities):
        intent = "connection_query"
        entities = connection_entities
        resolved_entity_ids = connection_entities[:2]

    document_refs = list(dict.fromkeys(DOCUMENT_REFERENCE_PATTERN.findall(query)))
    if document_refs:
        if _contains_any_token(q, DOCUMENT_SUMMARY_TOKENS):
            intent = "document_summary_query"
        elif intent == "general_query":
            intent = "document_query"

    is_list_query = _contains_any_token(q, LIST_QUERY_TOKENS)
    relationship_focus = _contains_any_token(q, RELATIONSHIP_HINTS)
    recall_priority = _contains_any_token(q, HIGH_RECALL_HINTS)
    inventory_target = _inventory_target_for_query(q) if is_list_query else None

    # Route short bare entity-type searches like "phone numbers" or "emails"
    # to deterministic inventory mode instead of the free-form query pipeline.
    bare_inventory_query = (
        inventory_target is None
        and not exact_tokens
        and not entities
        and len(re.findall(r"\w+", q)) <= 4
    )
    if bare_inventory_query:
        inventory_target = _inventory_target_for_query(q)

    if inventory_target:
        intent = (
            "entity_relationship_inventory_query"
            if relationship_focus
            else "entity_inventory_query"
        )

    return {
        "intent": intent,
        "entities": entities,
        "resolved_entity_ids": resolved_entity_ids,
        "document_refs": document_refs,
        "inventory_target": inventory_target,
        "relationship_focus": relationship_focus,
        "recall_priority": recall_priority,
        "raw_query": query,
    }
