from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/pdf")
async def generate_pdf_report() -> Dict[str, Any]:
    return {"message": "PDF report generation endpoint migrated to Python.", "status": "ok"}

