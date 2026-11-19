from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/verification", tags=["verification"])


@router.post("/run")
async def run_verification() -> Dict[str, Any]:
    return {"message": "Verification run endpoint migrated to Python.", "status": "pending"}


@router.get("/")
async def get_verification_history() -> Dict[str, Any]:
    return {"message": "Verification history endpoint migrated to Python.", "data": []}

