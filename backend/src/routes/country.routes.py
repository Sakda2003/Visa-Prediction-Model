from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/countries", tags=["countries"])


@router.get("/")
async def list_countries() -> Dict[str, Any]:
    return {"message": "List countries endpoint migrated to Python.", "data": []}


@router.post("/")
async def create_country() -> Dict[str, Any]:
    return {"message": "Create country endpoint migrated to Python.", "status": "ok"}


@router.put("/{country_id}")
async def update_country(country_id: str) -> Dict[str, Any]:
    return {"message": "Update country endpoint migrated to Python.", "country_id": country_id}


@router.delete("/{country_id}")
async def delete_country(country_id: str) -> Dict[str, Any]:
    return {"message": "Delete country endpoint migrated to Python.", "country_id": country_id}
