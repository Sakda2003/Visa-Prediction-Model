from __future__ import annotations
from typing import Any, Dict


async def upload_document(request: Any, response: Any = None) -> Dict[str, Any]:
    return {"status": "uploaded", "message": "Document uploaded"}
