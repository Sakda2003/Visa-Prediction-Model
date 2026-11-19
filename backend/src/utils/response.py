from __future__ import annotations

from typing import Any, Dict, Optional


def success_response(
    data: Any = None,
    message: str = "Success",
    status: int = 200,
) -> Dict[str, Any]:
    return {"message": message, "data": data, "status": status}


def error_response(
    message: str = "Error",
    status: int = 400,
    data: Optional[Any] = None,
) -> Dict[str, Any]:
    return {"message": message, "data": data, "status": status}

