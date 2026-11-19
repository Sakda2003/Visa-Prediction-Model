from __future__ import annotations
from typing import Any, Dict, Iterable, List


def load_mongo_collections(store: Dict[str, Any]) -> Dict[str, Any]:
    return store


def persist_entity(collection: str, entity: Dict[str, Any]) -> bool:
    return True


def remove_persisted_entity(collection: str, entity_id: str) -> bool:
    return True


def persist_demo_store(store: Dict[str, Any]) -> bool:
    return True
