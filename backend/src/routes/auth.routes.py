from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
async def register_user():
    return {"message": "User registration endpoint migrated to Python.", "status": "ok"}


@router.post("/login")
async def login_user():
    return {"message": "User login endpoint migrated to Python.", "status": "ok"}


@router.get("/me")
async def get_current_user():
    return {"message": "Current user endpoint migrated to Python.", "user": None}
