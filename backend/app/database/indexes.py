from typing import Any

from pymongo.asynchronous.database import AsyncDatabase


async def read_index_snapshot(
    database: AsyncDatabase,
) -> dict[str, list[dict[str, Any]]]:
    """Read existing index definitions without creating or changing indexes."""
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for collection_name in sorted(await database.list_collection_names()):
        indexes = await database[collection_name].list_indexes().to_list(length=None)
        snapshot[collection_name] = indexes
    return snapshot
