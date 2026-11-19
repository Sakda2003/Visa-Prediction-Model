from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/")
async def list_customers() -> Dict[str, Any]:
    return {"message": "List customers endpoint migrated to Python.", "data": []}


@router.post("/")
async def create_customer() -> Dict[str, Any]:
    return {"message": "Create customer endpoint migrated to Python.", "status": "ok"}


@router.put("/{customer_id}")
async def update_customer(customer_id: str) -> Dict[str, Any]:
    return {"message": "Update customer endpoint migrated to Python.", "customer_id": customer_id}


@router.delete("/{customer_id}")
async def delete_customer(customer_id: str) -> Dict[str, Any]:
    return {"message": "Delete customer endpoint migrated to Python.", "customer_id": customer_id}
