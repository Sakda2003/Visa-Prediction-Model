from __future__ import annotations

from typing import Any, Dict


async def delete_application(request: Any) -> Dict[str, Any]:
    request_data = request.json() if hasattr(request, "json") else {}
    application_id = request_data.get("applicationId") or request.path_params.get("id") if hasattr(request, "path_params") else None
    if not application_id:
        return {"message": "Application id is required"}
    return {"message": "Application deleted", "applicationId": application_id}
