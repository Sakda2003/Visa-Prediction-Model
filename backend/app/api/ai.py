from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from .dependencies import get_current_principal

router = APIRouter()


@router.post("/predict")
async def predict_visa(
    _payload: dict,
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "message": "No trained, validated visa prediction model is configured. No approval probability was generated.",
            "prediction_available": False,
        },
    )


@router.get("/predictions/{application_id}")
async def get_prediction(
    application_id: str,
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "message": "No saved model prediction exists for this application.",
            "applicationId": application_id,
        },
    )
