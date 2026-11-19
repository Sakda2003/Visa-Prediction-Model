"""Optional loopback-only first-admin setup for a new, empty local installation."""
import os
import re
from datetime import datetime, timezone
from uuid import uuid4
import bcrypt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError
from ..core.config import settings
from ..core.errors import APIError
from .dependencies import get_mongo_database
from .auth import _token_response, _user_id

router=APIRouter()

def local_enabled(request):
    local=request.client and request.client.host in {"127.0.0.1","::1"} and request.url.hostname in {"127.0.0.1","localhost","::1"}
    if not local or os.getenv("LOCAL_SETUP_ENABLED","").lower()!="true":
        raise APIError(404,"Setup is not enabled")
    origin=request.headers.get("origin")
    allowed={str(request.base_url).rstrip('/'),*settings.cors_origins}
    if origin and origin not in allowed:
        raise APIError(403,"Open setup from the local application address")

@router.get("/status")
async def status(request:Request,database=Depends(get_mongo_database)):
    try:local_enabled(request)
    except APIError:return {"requiresSetup":False}
    return {"requiresSetup":await database["users"].count_documents({})==0}

@router.post("/initialize",status_code=201)
async def initialize(payload:dict,request:Request,database=Depends(get_mongo_database)):
    local_enabled(request)
    if await database["users"].count_documents({}):raise APIError(409,"This installation already has users; use the normal sign-in or administrator recovery process")
    name=str(payload.get("name") or "").strip();email=str(payload.get("email") or "").strip().lower();password=payload.get("password")
    if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email) or not isinstance(password,str) or len(password)<12 or len(password.encode())>72:
        raise APIError(400,"Enter a name, valid email, and password of at least 12 characters (at most 72 UTF-8 bytes)")
    claim=str(uuid4())
    try:await database["system_setup"].insert_one({"_id":"first-admin","claim":claim,"createdAt":datetime.now(timezone.utc)})
    except DuplicateKeyError as exc:raise APIError(409,"Initial setup was already claimed; contact the local administrator") from exc
    user={"_id":_user_id(email),"name":name,"email":email,"passwordHash":bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode(),"role":"ADMIN","createdAt":datetime.now(timezone.utc)}
    try:
        if await database["users"].count_documents({}):raise APIError(409,"Another user was created during setup; use normal sign-in")
        await database["users"].insert_one(user)
    except Exception:
        await database["system_setup"].delete_one({"_id":"first-admin","claim":claim})
        raise
    return JSONResponse(status_code=201,content=_token_response(user))
