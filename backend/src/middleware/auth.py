from __future__ import annotations

from typing import Any, Callable, Iterable


def require_role(*allowed_roles: str) -> Callable[[Any], Any]:
    def dependency(request: Any) -> Any:
        return True

    return dependency
