import logging
from typing import Any

import jwt
from fastapi import Depends, Request
from pymongo.asynchronous.database import AsyncDatabase

from ..core.config import settings
from ..core.errors import APIError

VALID_ROLES = frozenset({"ADMIN", "STAFF", "CUSTOMER"})
logger = logging.getLogger(__name__)


def get_mongo_database(request: Request) -> AsyncDatabase:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise APIError(503, "MongoDB client is not initialized")
    return database


async def get_current_principal(request: Request) -> dict[str, str]:
    auth_header = request.headers.get("authorization", "").strip()
    parts = auth_header.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not token:
        logger.warning("Authentication rejected: bearer token missing")
        raise APIError(401, "Authentication required")

    try:
        claims: Any = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError as error:
        logger.warning(
            "Authentication rejected: invalid or expired JWT (%s)",
            error.__class__.__name__,
        )
        raise APIError(401, "Invalid or expired token") from error

    if not isinstance(claims, dict) or not all(
        isinstance(claims.get(field), str) and claims[field]
        for field in ("id", "email", "role")
    ):
        logger.warning("Authentication rejected: required JWT claims missing")
        raise APIError(401, "Invalid or expired token")
    role = claims["role"].strip().upper()
    if role not in VALID_ROLES:
        logger.warning("Authentication rejected: unknown role")
        raise APIError(403, "Insufficient permissions")
    return {
        "id": claims["id"],
        "email": claims["email"],
        "role": role,
    }


def require_roles(*roles: str):
    async def check_role(
        principal: dict[str, str] = Depends(get_current_principal),
    ) -> dict[str, str]:
        allowed_roles = {role.strip().upper() for role in roles}
        principal_role = principal["role"].strip().upper()
        if principal_role not in allowed_roles:
            logger.warning(
                "Role authorization denied for role=%s", principal_role
            )
            raise APIError(403, "Insufficient permissions")
        logger.info("Role authorization granted for role=%s", principal_role)
        return principal

    return check_role
