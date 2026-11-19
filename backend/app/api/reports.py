from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import Workbook
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.errors import APIError
from .dependencies import get_mongo_database
from .mongo_helpers import public_document

logger = logging.getLogger(__name__)
router = APIRouter()


async def _report_rows(
    database: AsyncDatabase, start_date: str | None, end_date: str | None
) -> list[dict[str, Any]]:
    try:
        applications = await database["visaapplications"].find({}).to_list(length=None)
        customers = {
            str(item["_id"]): item
            for item in await database["customers"].find({}).to_list(length=None)
        }
        visa_types = {
            str(item["_id"]): item
            for item in await database["visatypes"].find({}).to_list(length=None)
        }
    except PyMongoError as error:
        logger.exception("Could not prepare report: %s", error.__class__.__name__)
        raise APIError(503, "Report data is unavailable") from error
    rows = []
    for application in applications:
        created = application.get("createdAt")
        created_text = created.isoformat() if isinstance(created, datetime) else str(created or "")
        if start_date and created_text[:10] < start_date:
            continue
        if end_date and created_text[:10] > end_date:
            continue
        result = public_document(application)
        customer = customers.get(str(application.get("customer") or ""))
        visa_type = visa_types.get(str(application.get("visaTypeId") or ""))
        result["applicant"] = customer.get("fullName", "") if customer else ""
        result["country"] = visa_type.get("destination", application.get("destination", "")) if visa_type else application.get("destination", "")
        result["visaTypeName"] = visa_type.get("name", "") if visa_type else ""
        rows.append(result)
    return rows


@router.get("/statistics")
async def report_statistics(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    rows = await _report_rows(database, start_date, end_date)
    statuses = [str(row.get("status") or "").upper() for row in rows]
    countries: dict[str, int] = {}
    for row in rows:
        country = str(row.get("country") or "Unknown")
        countries[country] = countries.get(country, 0) + 1
    return JSONResponse(
        content={
            "totalApplications": len(rows),
            "approved": sum(status in {"APPROVED", "FINAL_PASS"} for status in statuses),
            "rejected": sum(status in {"REJECTED", "FINAL_FAIL"} for status in statuses),
            "pending": sum(status not in {"APPROVED", "FINAL_PASS", "REJECTED", "FINAL_FAIL"} for status in statuses),
            "countryStatistics": [{"country": key, "applications": value} for key, value in sorted(countries.items())],
            "applications": rows,
        }
    )


@router.get("/export/csv")
async def export_csv(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> StreamingResponse:
    rows = await _report_rows(database, start_date, end_date)
    output = io.StringIO(newline="")
    columns = ("applicationNumber", "applicant", "country", "visaTypeName", "status", "completenessScore", "createdAt")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="visa-applications.csv"'},
    )


@router.get("/export/excel")
async def export_excel(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> StreamingResponse:
    rows = await _report_rows(database, start_date, end_date)
    columns = ("applicationNumber", "applicant", "country", "visaTypeName", "status", "completenessScore", "createdAt")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Applications"
    sheet.append(list(columns))
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="visa-applications.xlsx"'},
    )
