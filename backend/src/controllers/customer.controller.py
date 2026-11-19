from __future__ import annotations
from typing import Any, Dict


async def list_customers(request: Any, response: Any = None) -> Dict[str, Any]:
    return {"customers": []}


async def create_customer(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    return {"message": "Customer created", **payload}
