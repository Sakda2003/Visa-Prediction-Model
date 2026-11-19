from __future__ import annotations
from typing import Any, Dict


def create_demo_store() -> Dict[str, Any]:
    return {
        "users": [],
        "customers": [],
        "countries": [],
        "visaTypes": [],
        "requirements": [],
        "applications": [],
        "documents": [],
        "verifications": [],
    }


async def connect_database() -> Dict[str, Any]:
    return {"status": "connected"}


async def close_database() -> None:
    return None


async def seed_data() -> Dict[str, Any]:
    return {"status": "seeded"}
