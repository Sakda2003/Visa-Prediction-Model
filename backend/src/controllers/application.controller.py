from __future__ import annotations
from typing import Any, Dict


async def delete_application(request: Any, response: Any = None) -> Dict[str, Any]:
    payload = request.json() if hasattr(request, "json") else {}
    application_id = payload.get("applicationId") or getattr(request, "path_params", {}).get("id")
    if not application_id:
        return {"message": "Application id is required"}
    return {"message": "Application deleted", "applicationId": application_id}
