
from typing import Optional

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from ..core.config import settings


def create_mongo_client() -> AsyncMongoClient:
    return AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        maxPoolSize=50,
    )


def get_database(client: Optional[AsyncMongoClient] = None) -> AsyncDatabase:
    mongo_client = client
    if mongo_client is None:
        raise RuntimeError("MongoDB client is not initialized")
    return mongo_client.get_database(settings.mongodb_database)
