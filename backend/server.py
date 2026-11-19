
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# ==========================================
# ENVIRONMENT CONFIGURATION
# ==========================================

load_dotenv()

HOST = os.getenv("PYTHON_HOST", "127.0.0.1")
PORT = int(os.getenv("PYTHON_PORT", "8001"))

NODE_SERVICE_URL = os.getenv(
    "NODE_SERVICE_URL",
    "http://127.0.0.1:5000"
)

NODE_ENV = os.getenv("NODE_ENV", "development")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("visadoc-ai-python")


# ==========================================
# APPLICATION LIFESPAN
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Starting VisaDoc AI Python Service")

    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0)
    )

    yield

    await app.state.http_client.aclose()

    logger.info("Python Service stopped")


# ==========================================
# FASTAPI APPLICATION
# ==========================================

app = FastAPI(
    title="VisaDoc AI Python Service",
    description="OCR, AI Document Analysis and Verification",
    version="1.0.0",
    lifespan=lifespan
)


# ==========================================
# CORS CONFIGURATION
# ==========================================

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


# ==========================================
# REQUEST MODELS
# ==========================================

class HealthResponse(BaseModel):
    success: bool
    status: str
    service: str
    version: str
    timestamp: str


# ==========================================
# ROOT ENDPOINT
# ==========================================

@app.get("/")
async def root():

    return {
        "success": True,
        "service": "VisaDoc AI Python Service",
        "message": "Python AI Service is running",
        "docs": "/docs"
    }


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/health", response_model=HealthResponse)
async def health_check():

    return HealthResponse(
        success=True,
        status="ok",
        service="VisaDoc AI Python Service",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc).isoformat()
    )


# ==========================================
# NODE.JS CONNECTION CHECK
# ==========================================

@app.get("/api/node-health")
async def check_node_connection():

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:

            response = await client.get(
                f"{NODE_SERVICE_URL}/api/health"
            )

            response.raise_for_status()

            return {
                "success": True,
                "node_service": "connected",
                "node_response": response.json()
            }

    except httpx.HTTPError as error:

        logger.error("Node.js connection failed: %s", error)

        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Node.js Backend"
        )


# ==========================================
# AI SERVICE STATUS
# ==========================================

@app.get("/api/ai/status")
async def ai_status():

    return {
        "success": True,
        "service": "VisaDoc AI",
        "modules": {
            "ocr": "ready",
            "pdf_processing": "ready",
            "image_processing": "ready",
            "document_verification": "integration_required"
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ==========================================
# GLOBAL ERROR HANDLER
# ==========================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):

    logger.exception("Unhandled Python Service Error")

    return __import__("fastapi").responses.JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": (
                "Internal Python AI Service error"
                if NODE_ENV == "production"
                else str(exc)
            )
        }
    )


# ==========================================
# SERVER STARTUP
# ==========================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "server:app",
        host=HOST,
        port=PORT,
        reload=NODE_ENV == "development"
    )
