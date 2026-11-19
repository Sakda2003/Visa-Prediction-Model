"""Case-scoped exports reuse the canonical report rather than recomputing scores."""
import csv
import html
import io
import json
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from ..core.errors import APIError
from .dependencies import get_mongo_database, require_roles
from .verification_reports import get_verification_results
router=APIRouter()

@router.get("/{application_id}/exports/{kind}")
async def export_assessment(application_id:str,kind:str,database=Depends(get_mongo_database),principal=Depends(require_roles("ADMIN","STAFF","CUSTOMER"))):
    response=await get_verification_results(application_id,database,principal)
    result=json.loads(response.body)
    summary=result["summary"]
    headers={"Cache-Control":"no-store","Content-Disposition":f'attachment; filename="evidence-assessment.{kind}"'}
    if kind=="json":
        return Response(json.dumps(result,ensure_ascii=False,indent=2),media_type="application/json",headers=headers)
    if kind=="csv":
        stream=io.StringIO(newline="");writer=csv.writer(stream)
        writer.writerow(["Requirement slot","Mode","Confirmed points","Exact points","Maximum points","Quality (0-1)","Status","Policy version","Reason"])
        def cell(v):
            text=str(v if v is not None else "")
            return "'"+text if text.lstrip().startswith(("=","+","-","@")) else text
        for slot in summary.get("requirementScores",[]):
            writer.writerow([cell(v) for v in [slot.get("name"),slot.get("scoringMode"),slot.get("earnedPoints"),slot.get("exactEarnedPoints"),slot.get("configuredPoints"),slot.get("quality"),slot.get("status"),summary.get("policyVersionId"),"; ".join(slot.get("reasons") or [])]])
        return Response("\ufeff"+stream.getvalue(),media_type="text/csv",headers=headers)
    if kind=="html":
        esc=lambda v:html.escape(str(v if v is not None else "Unavailable"))
        slots="".join(f'<tr><td>{esc(s.get("name"))}</td><td>{esc(s.get("scoringMode"))}</td><td>{esc(s.get("earnedPoints"))}</td><td>{esc(s.get("configuredPoints"))}</td><td>{esc(s.get("status"))}</td><td>{esc("; ".join(s.get("reasons") or []))}</td></tr>' for s in summary.get("requirementScores",[]))
        prediction=result.get("prediction",{});probability=prediction.get("approvalProbabilityNormalized")
        p="Unavailable — no current validated estimate for this case" if probability is None or prediction.get("status")!="COMPLETED" else f'{probability*100:.1f}% (decision support only)'
        body=f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Evidence assessment</title><style>body{{font:15px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:20px;color:#0f172a}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style><h1>Evidence assessment</h1><p>{esc(result.get("applicant",{}).get("fullName"))} · {esc(result.get("application",{}).get("applicationNumber"))}</p><h2>{esc(summary.get("confirmedReadinessPoints",summary.get("documentAssessmentScore")))} / 100 confirmed points</h2><p>Policy {esc(summary.get("policyVersionId"))}; status {esc(summary.get("status",summary.get("documentProcessingStatus")))}.</p><p>Readiness points are not an approval probability. Estimated approval probability: {esc(p)}. Authenticity is not established by arithmetic consistency.</p><table><tr><th>Requirement</th><th>Mode</th><th>Earned</th><th>Maximum</th><th>Status</th><th>Reason</th></tr>{slots}</table><h2>Policy, provenance and source checks</h2><pre>{esc(json.dumps({"requirements":result.get("requirements"),"assessment":summary,"prediction":prediction},ensure_ascii=False,indent=2))}</pre></html>'
        return Response(body,media_type="text/html",headers=headers)
    raise APIError(404,"Choose json, csv or html export")
