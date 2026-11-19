from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import APIRouter, Depends, Request

from ..controllers.application_controller import delete_application
from ..controllers.visa_controller import (
    archive_visa_type,
    create_application,
    create_requirement,
    create_visa_type,
    delete_visa_type,
    duplicate_visa_type,
    get_visa_type,
    list_applications,
    list_requirements,
    list_visa_types,
    update_visa_type,
)
from ..middleware.auth import require_role

router = APIRouter()


def role_admin() -> Callable[..., Any]:
    return Depends(require_role("ADMIN"))


def role_admin_staff() -> Callable[..., Any]:
    return Depends(require_role("ADMIN", "STAFF"))


@router.get("/")
async def get_visa_root(request: Request) -> Dict[str, Any]:
    return await list_visa_types(request)


@router.post("/")
async def create_visa_root(request: Request) -> Dict[str, Any]:
    return await create_visa_type(request)


@router.get("/types")
async def get_visa_types(request: Request) -> Dict[str, Any]:
    return await list_visa_types(request)


@router.post("/types")
async def create_visa_type_route(request: Request) -> Dict[str, Any]:
    return await create_visa_type(request)


@router.get("/requirements")
async def get_requirements(request: Request) -> Dict[str, Any]:
    return await list_requirements(request)


@router.post("/requirements")
async def create_requirement_route(request: Request) -> Dict[str, Any]:
    return await create_requirement(request)


@router.get("/applications")
async def get_applications(request: Request) -> Dict[str, Any]:
    return await list_applications(request)


@router.post("/applications")
async def create_application_route(request: Request) -> Dict[str, Any]:
    return await create_application(request)


@router.delete("/applications/{application_id}")
async def delete_application_route(request: Request, application_id: str) -> Dict[str, Any]:
    request.path_params = {"id": application_id}
    return await delete_application(request)


@router.post("/{visa_id}/duplicate")
async def duplicate_visa_type_route(request: Request, visa_id: str) -> Dict[str, Any]:
    request.path_params = {"id": visa_id}
    return await duplicate_visa_type(request)


@router.patch("/{visa_id}/archive")
async def archive_visa_type_route(request: Request, visa_id: str) -> Dict[str, Any]:
    request.path_params = {"id": visa_id}
    return await archive_visa_type(request)


@router.get("/{visa_id}")
async def get_visa_detail(request: Request, visa_id: str) -> Dict[str, Any]:
    request.path_params = {"id": visa_id}
    return await get_visa_type(request)


@router.put("/{visa_id}")
async def update_visa_detail(request: Request, visa_id: str) -> Dict[str, Any]:
    request.path_params = {"id": visa_id}
    return await update_visa_type(request)


@router.delete("/{visa_id}")
async def delete_visa_detail(request: Request, visa_id: str) -> Dict[str, Any]:
    request.path_params = {"id": visa_id}
    return await delete_visa_type(request)

