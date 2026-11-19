import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    database_status = getattr(request.app.state, "database_status", "connected")
    connected = database_status == "connected"
    return {
        "success": connected,
        "status": "ok" if connected else "degraded",
        "service": "VisaDoc AI FastAPI backend",
        "database": {
            "type": "MongoDB",
            "status": database_status,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness(request: Request) -> dict[str, object]:
    client = getattr(request.app.state, "mongo_client", None)
    database = getattr(request.app.state, "database", None)
    if client is None or database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is unavailable",
        )
    admin = getattr(client, "admin", None)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB client is not initialized",
        )
    try:
        await admin.command("ping")
    except PyMongoError as error:
        logger.warning("MongoDB readiness check failed: %s", error.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is unavailable",
        ) from error
    return {"success": True, "status": "ready"}
