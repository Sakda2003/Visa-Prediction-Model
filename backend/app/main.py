
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.exceptions import RequestValidationError
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.countries import router as countries_router
from .api.customers import router as customers_router
from .api.auth import router as auth_router
from .api.health import router as health_router
from .api.dashboard import router as dashboard_router
from .api.visa_types import applications_router, router as visa_types_router
from .api.requirements import router as requirements_router
from .api.documents import router as documents_router
from .api.verification import router as verification_router
from .api.verification_reports import (
    public_router as public_receipts_router,
    router as verification_reports_router,
)
from .api.ocr import router as ocr_router
from .api.bank_statements import router as bank_statements_router
from .api.ai import router as ai_router
from .api.ml import router as ml_router
from .api.reports import router as reports_router
from .api.users import router as users_router
from .api.policy_profiles import router as policy_profiles_router
from .api.first_run import router as first_run_router
from .api.assessment_exports import router as assessment_exports_router

from .api.dependencies import get_current_principal, require_roles
from .core.errors import APIError
from .core.config import settings
from .core.logging_config import configure_logging
from .core.rate_limit import ProcessLocalRateLimiter
from .database.mongodb import create_mongo_client


# --------------------------------------------------
# LOGGING
# --------------------------------------------------

configure_logging()

logger = logging.getLogger(__name__)

rate_limiter = ProcessLocalRateLimiter()


# --------------------------------------------------
# MONGODB LIFESPAN
# --------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:

    client = None

    application.state.mongo_client = None
    application.state.database = None
    application.state.database_status = "disconnected"

    try:
        logger.info("Connecting to MongoDB...")

        client = create_mongo_client()
        application.state.mongo_client = client

        if not hasattr(client, "get_database") or not hasattr(client, "admin"):
            raise TypeError("MongoDB client does not support database selection and ping")
        application.state.database = client.get_database(settings.mongodb_database)
        await client.admin.command("ping")
        application.state.database_status = "connected"
        logger.info(
            "MongoDB connected successfully. Database: %s",
            settings.mongodb_database,
        )

    except (PyMongoError, TypeError, ValueError):
        logger.exception("MongoDB connection failed")

        application.state.database_status = "disconnected"

        if client is not None:
            try:
                await client.close()
            except PyMongoError:
                logger.exception("MongoDB client cleanup after startup failure failed")
            client = None

        application.state.mongo_client = None
        application.state.database = None

    try:
        yield

    finally:
        logger.info("Shutting down application...")

        try:
            if client is not None:
                await client.close()
        except PyMongoError:
            logger.exception("MongoDB client cleanup during shutdown failed")
        finally:
            application.state.mongo_client = None
            application.state.database = None
            application.state.database_status = "disconnected"

        logger.info("Application shutdown complete.")


# --------------------------------------------------
# FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="VISA Prediction System API",
    lifespan=lifespan,
)


# --------------------------------------------------
# CORS CONFIGURATION
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in settings.cors_origins if origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------
# RATE LIMITING
# --------------------------------------------------

@app.middleware("http")
async def apply_rate_limits(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:

    path = request.url.path

    if path == "/api" or path.startswith("/api/"):

        client_ip = (
            request.client.host
            if request.client
            else "unknown"
        )

        # General API rate limit
        if not rate_limiter.allow(
            "api",
            client_ip,
            1000,
            15 * 60,
        ):
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "message": "Too many API requests. Please try again later.",
                },
            )

        # Login rate limit
        if path == "/api/auth/login":

            if not rate_limiter.allow(
                "login",
                client_ip,
                10,
                15 * 60,
            ):
                return PlainTextResponse(
                    "Too many requests, please try again later.",
                    status_code=429,
                )

    return await call_next(request)


# --------------------------------------------------
# GLOBAL API ERROR HANDLER
# --------------------------------------------------

@app.exception_handler(APIError)
async def handle_api_error(
    request: Request,
    error: APIError,
) -> JSONResponse:

    logger.warning(
        "API error: %s %s - %s",
        request.method,
        request.url.path,
        error.message,
    )

    return JSONResponse(
        status_code=error.status_code,
        content={
            "message": error.message,
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"message": "Validation error"},
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    messages = {
        400: "Validation error",
        401: "Authentication required",
        403: "Insufficient permission",
        404: "Resource not found",
        503: "Service unavailable",
    }
    return JSONResponse(
        status_code=error.status_code,
        content={"message": messages.get(error.status_code, "Request failed")},
    )


# --------------------------------------------------
# GENERAL EXCEPTION HANDLER
# --------------------------------------------------

@app.exception_handler(Exception)
async def handle_unexpected_error(
    request: Request,
    error: Exception,
) -> JSONResponse:

    logger.exception(
        "Unexpected error on %s %s",
        request.method,
        request.url.path,
        exc_info=error,
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "Internal server error.",
        },
    )


# --------------------------------------------------
# BASIC ROOT ENDPOINT
# --------------------------------------------------

@app.get("/")
async def root():
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    if frontend.is_file():
        return FileResponse(frontend,headers={"Cache-Control":"no-store"})
    return {
        "success": True,
        "service": "VISA Prediction System",
        "version": settings.app_version,
        "documentation": "/docs",
        "health": "/api/health",
    }


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/api/system-health")
async def system_health(request: Request):

    database_status = "disconnected"

    client = getattr(
        request.app.state,
        "mongo_client",
        None,
    )

    if client is not None and getattr(request.app.state, "database", None) is not None:

        try:
            await client.admin.command("ping")
            database_status = "connected"

        except PyMongoError:
            logger.exception("MongoDB health check failed")
            database_status = "disconnected"

    overall_status = (
        "ok"
        if database_status == "connected"
        else "degraded"
    )

    return {
        "success": database_status == "connected",
        "status": overall_status,
        "service": settings.app_name,
        "version": settings.app_version,
        "database": {
            "type": "MongoDB",
            "status": database_status,
            "name": settings.mongodb_database,
        },
    }


# --------------------------------------------------
# API ROUTERS
# --------------------------------------------------

app.include_router(
    health_router,
    prefix="/api",
    tags=["health"],
)

app.include_router(
    auth_router,
    prefix="/api/auth",
    tags=["auth"],
)

app.include_router(
    countries_router,
    prefix="/api/countries",
    tags=["countries"],
    dependencies=[
        Depends(get_current_principal)
    ],
)

app.include_router(
    customers_router,
    prefix="/api/customers",
    tags=["customers"],
    dependencies=[Depends(require_roles("ADMIN", "STAFF"))],
)

app.include_router(
    dashboard_router,
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_roles("ADMIN", "STAFF"))],
)

app.include_router(
    visa_types_router,
    prefix="/api/visa-types",
    tags=["visa-types"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    applications_router,
    prefix="/api/applications",
    tags=["applications"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    requirements_router,
    prefix="/api/requirements",
    tags=["requirements"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    documents_router,
    prefix="/api/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    verification_router,
    prefix="/api/verification",
    tags=["verification"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    verification_reports_router,
    prefix="/api/verification",
    tags=["verification-reports-and-receipts"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    public_receipts_router,
    prefix="/api/verification/receipts/verify",
    tags=["public-receipt-verification"],
)

app.include_router(
    ocr_router,
    prefix="/api/ocr",
    tags=["ocr"],
)

app.include_router(
    bank_statements_router,
    prefix="/api/bank-statements",
    tags=["bank-statements"],
)

app.include_router(
    ai_router,
    prefix="/api/ai",
    tags=["ai"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    ml_router,
    prefix="/api",
    tags=["machine-learning-and-business-rules"],
)

app.include_router(
    reports_router,
    prefix="/api/reports",
    tags=["reports"],
    dependencies=[Depends(require_roles("ADMIN", "STAFF"))],
)

app.include_router(
    users_router,
    prefix="/api/users",
    tags=["users"],
)

app.include_router(
    policy_profiles_router,
    prefix="/api/policy-profiles",
    tags=["evidence-policy-profiles"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(first_run_router,prefix="/api/setup",tags=["local-first-run"])
app.include_router(assessment_exports_router,prefix="/api/assessments",tags=["case-assessment-exports"])

# The same authenticated API serves the production UI; private storage is never mounted.
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets",StaticFiles(directory=_frontend_dist/"assets"),name="frontend-assets")
    @app.get("/{ui_path:path}", include_in_schema=False)
    async def ui_route(ui_path:str):
        if ui_path.startswith("api/"):
            return JSONResponse(status_code=404,content={"message":"Resource not found"})
        # Only explicitly public PWA/brand assets and the SPA shell can be served.
        if ui_path in {"manifest.webmanifest","sw.js","offline.html","favicon.svg","icons.svg","pwa-icon.svg","global-eagle-logo.jpg","login-skyline.svg"}:
            return FileResponse(_frontend_dist/ui_path)
        return FileResponse(_frontend_dist/"index.html",headers={"Cache-Control":"no-store"})
