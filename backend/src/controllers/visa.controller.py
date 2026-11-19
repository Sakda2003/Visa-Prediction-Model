from __future__ import annotations
from typing import Any, Dict


async def list_visa_types(request: Any, response: Any = None) -> Dict[str, Any]:
    return {"visaTypes": []}


async def create_visa_type(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    return {"message": "Visa type created", **payload}
