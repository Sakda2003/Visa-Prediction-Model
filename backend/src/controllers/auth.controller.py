from __future__ import annotations
from typing import Any, Dict


async def register_user(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    if not name or not email:
        return {"message": "Name and email are required"}
    return {"message": "User registered", "name": name, "email": email}


async def login_user(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not email or not password:
        return {"message": "Email and password are required"}
    return {"message": "Login successful", "email": email}
