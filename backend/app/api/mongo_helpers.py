from __future__ import annotations

import re
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from ..database.serialization import serialize_mongo_document


def id_candidates(value: str) -> list[Any]:
    candidates: list[Any] = [value]
    try:
        object_id = ObjectId(value)
    except (InvalidId, TypeError):
        return candidates
    candidates.append(object_id)
    return candidates


def id_query(value: str) -> dict[str, Any]:
    return {"_id": {"$in": id_candidates(value)}}


def reference_query(field: str, value: str) -> dict[str, Any]:
    return {field: {"$in": id_candidates(value)}}


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    result = serialize_mongo_document(document)
    result.pop("__v", None)
    result.pop("password", None)
    result.pop("passwordHash", None)
    result.pop("password_hash", None)
    result.pop("storagePath", None)
    result.pop("contentHash", None)
    result.pop("document_reference", None)
    result.pop("ocrText", None)
    fields = document.get("extractedFields")
    account_number = (
        fields.get("accountNumber") or fields.get("accountNo")
        if isinstance(fields, dict)
        else None
    )
    return redact_sensitive_data(
        result,
        {str(account_number)} if account_number else set(),
    )


def _masked_account_number(value: Any) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    return (
        f"{'*' * min(8, len(compact) - 4)}{compact[-4:]}"
        if len(compact) > 4
        else "****"
    )


def redact_sensitive_data(
    value: Any, account_numbers: set[str] | None = None
) -> Any:
    known_numbers = account_numbers or set()
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized_key in {"accountnumber", "accountno"} and item:
                redacted[key] = _masked_account_number(item)
            else:
                redacted[key] = redact_sensitive_data(item, known_numbers)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item, known_numbers) for item in value]
    if isinstance(value, str):
        sanitized = value
        for account_number in known_numbers:
            compact = re.sub(r"[^A-Za-z0-9]", "", account_number)
            if len(compact) < 5:
                continue
            for variant in {account_number, compact, " ".join(compact)}:
                sanitized = re.sub(
                    re.escape(variant),
                    _masked_account_number(compact),
                    sanitized,
                    flags=re.IGNORECASE,
                )
        return re.sub(
            r"(?im)(account\s*(?:number|no\.?|#)\s*[:#-]?\s*)([A-Z0-9][A-Z0-9 -]{3,})",
            lambda match: match.group(1) + _masked_account_number(match.group(2)),
            sanitized,
        )
    return value
