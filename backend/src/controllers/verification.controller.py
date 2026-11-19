from __future__ import annotations
from typing import Any, Dict


async def run_verification(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    return {"status": "review", "applicationId": payload.get("applicationId"), "decision": "REVIEW"}
