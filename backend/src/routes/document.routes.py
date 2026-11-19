from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/")
async def list_documents() -> Dict[str, Any]:
    return {"message": "List documents endpoint migrated to Python.", "data": []}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    return {
        "message": "Document upload endpoint migrated to Python.",
        "status": "uploaded",
        "filename": file.filename,
    }


@router.get("/{document_id}/content")
async def stream_document(document_id: str) -> Dict[str, Any]:
    return {"message": "Stream document endpoint migrated to Python.", "document_id": document_id}


@router.delete("/{document_id}")
async def remove_document(document_id: str) -> Dict[str, Any]:
    return {"message": "Remove document endpoint migrated to Python.", "document_id": document_id}


@router.patch("/{document_id}/review")
async def review_document(document_id: str) -> Dict[str, Any]:
    return {"message": "Review endpoint migrated to Python.", "document_id": document_id}

