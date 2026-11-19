from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
async def get_dashboard() -> Dict[str, Any]:
    return {"message": "Dashboard endpoint migrated to Python.", "data": {"totals": 0}}
