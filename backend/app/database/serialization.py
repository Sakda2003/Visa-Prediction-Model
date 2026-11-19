from datetime import datetime, timezone
from typing import Any

from bson import ObjectId


def serialize_mongo_document(document: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in document.items()}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value
