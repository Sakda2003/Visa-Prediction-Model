from __future__ import annotations
from typing import Any, Dict


async def list_countries(request: Any, response: Any = None) -> Dict[str, Any]:
    return {"countries": []}


async def create_country(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    return {"message": "Country created", **payload}
