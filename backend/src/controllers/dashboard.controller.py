from __future__ import annotations
from typing import Any, Dict


async def get_dashboard(request: Any, response: Any = None) -> Dict[str, Any]:
    return {
        "customers": 0,
        "applications": 0,
        "documents": 0,
        "statusCounts": {},
        "monthlyCounts": {},
    }
