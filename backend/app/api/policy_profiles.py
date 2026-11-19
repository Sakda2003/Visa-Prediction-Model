from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..services.evidence_scoring import VERSION, calculate_evidence_assessment
from ..services.policy_profiles import content_hash, load_published_profile, profile_snapshot, requirement_snapshot, validate_profile
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document, reference_query

router=APIRouter()
COLLECTION="evidencepolicyprofiles"
EDITABLE={"name","version","requirements","effectiveFrom","effectiveUntil","timezone","description"}


@router.get("")
@router.get("/")
async def list_profiles(visa_type_id: str|None=Query(None,alias="visaTypeId"),database:AsyncDatabase=Depends(get_mongo_database),_principal:dict[str,str]=Depends(require_roles("ADMIN","STAFF"))):
    rows=await database[COLLECTION].find({}).to_list(length=None)
    rows=[row for row in rows if not visa_type_id or str(row.get("visaTypeId"))==visa_type_id]
    rows.sort(key=lambda r:str(r.get("createdAt") or ""),reverse=True)
    return {"profiles":[public_document(row) for row in rows]}


@router.post("",status_code=201)
@router.post("/",status_code=201)
async def create_profile(payload:dict[str,Any],database:AsyncDatabase=Depends(get_mongo_database),principal:dict[str,str]=Depends(require_roles("ADMIN"))):
    visa=await database["visatypes"].find_one(id_query(str(payload.get("visaTypeId") or "")))
    if not visa:raise APIError(404,"Visa type not found")
    requirements=payload.get("requirements")
    if requirements is None:
        rows=await database["visarequirements"].find(reference_query("visaTypeId",str(visa["_id"]))).to_list(length=None)
        requirements=[requirement_snapshot(row) for row in rows if str(row.get("visaTypeId"))==str(visa["_id"]) and row.get("status","ACTIVE")=="ACTIVE"]
    now=datetime.now(timezone.utc)
    data={key:deepcopy(payload[key]) for key in EDITABLE if key in payload}
    data.update(_id=str(uuid4()),visaTypeId=str(visa["_id"]),countryId=str(visa.get("countryId") or ""),requirements=requirements,status="DRAFT",revision=1,version=str(payload.get("version") or "1"),createdAt=now,updatedAt=now,createdBy=principal["id"])
    try:data=validate_profile(data)
    except (ValueError,TypeError) as exc:raise APIError(400,str(exc)) from exc
    await database[COLLECTION].insert_one(data)
    return JSONResponse(status_code=201,content={"profile":public_document(data)})


@router.get("/effective/visa-type/{visa_type_id}")
async def effective_visa_profile(
    visa_type_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF", "CUSTOMER")),
):
    """The same effective immutable definition used at application creation.

    This read exposes only a published requirement snapshot. Draft administration
    stays restricted to staff/admin and never substitutes mutable requirements.
    """
    visa = await database["visatypes"].find_one(id_query(visa_type_id))
    if not visa:
        raise APIError(404, "Visa type not found")
    if not visa.get("active", True):
        return {"profile": None}
    profile = await load_published_profile(database, visa["_id"])
    return {"profile": public_document(profile_snapshot(profile)) if profile else None}


@router.get("/{profile_id}")
async def get_profile(profile_id:str,database:AsyncDatabase=Depends(get_mongo_database),_principal:dict[str,str]=Depends(require_roles("ADMIN","STAFF"))):
    row=await database[COLLECTION].find_one(id_query(profile_id))
    if not row:raise APIError(404,"Profile not found")
    return {"profile":public_document(row)}


@router.put("/{profile_id}")
async def edit_profile(profile_id:str,payload:dict[str,Any],database:AsyncDatabase=Depends(get_mongo_database),principal:dict[str,str]=Depends(require_roles("ADMIN"))):
    current=await database[COLLECTION].find_one(id_query(profile_id))
    if not current:raise APIError(404,"Profile not found")
    if current.get("status")!="DRAFT":raise APIError(409,"Published profiles are immutable; create a new draft/version")
    if payload.get("revision")!=current.get("revision"):raise APIError(409,"Draft changed; reload before saving")
    data={**current,**{key:deepcopy(payload[key]) for key in EDITABLE if key in payload},"revision":current["revision"]+1,"updatedAt":datetime.now(timezone.utc),"updatedBy":principal["id"]}
    try:data=validate_profile(data)
    except (ValueError,TypeError) as exc:raise APIError(400,str(exc)) from exc
    result=await database[COLLECTION].replace_one({"_id":current["_id"],"status":"DRAFT","revision":current["revision"]},data)
    if not result.matched_count:raise APIError(409,"Draft changed; reload before saving")
    return {"profile":public_document(data)}


@router.post("/{profile_id}/publish",status_code=201)
async def publish_profile(profile_id:str,payload:dict[str,Any],database:AsyncDatabase=Depends(get_mongo_database),principal:dict[str,str]=Depends(require_roles("ADMIN"))):
    draft=await database[COLLECTION].find_one(id_query(profile_id))
    if not draft:raise APIError(404,"Profile not found")
    if draft.get("status")!="DRAFT":raise APIError(409,"Only a draft can be published")
    if payload.get("revision")!=draft.get("revision"):raise APIError(409,"Draft changed; reload before publishing")
    reason=str(payload.get("reason") or "").strip()
    if len(reason)<5:raise APIError(400,"A publication review reason is required")
    try:validated=validate_profile(draft,publishing=True)
    except (ValueError,TypeError) as exc:raise APIError(400,str(exc)) from exc
    # The entire validated policy is one immutable MongoDB document. No live per-row edits occur during publication.
    now=datetime.now(timezone.utc)
    release={**validated,"_id":str(uuid4()),"status":"PUBLISHED","parentDraftId":profile_id,"publishedAt":now,"publishedBy":principal["id"],"publicationReason":reason,"createdAt":now,"updatedAt":now}
    release["contentHash"]=content_hash(release)
    existing=await database[COLLECTION].find({"visaTypeId":release["visaTypeId"],"status":"PUBLISHED"}).to_list(length=None)
    if any(str(r.get("visaTypeId"))==release["visaTypeId"] and r.get("status")=="PUBLISHED" and r.get("version")==release["version"] for r in existing):
        raise APIError(409,"This visa policy version is already published; increment the version")
    # Deterministic publication ID makes concurrent publishing of the same visa/version conflict atomically.
    import hashlib
    release["_id"]="policy-"+hashlib.sha256((release["visaTypeId"]+":"+release["version"]).encode()).hexdigest()[:32]
    # Linearize the reviewed draft revision before inserting the immutable release.
    # A racing draft edit fails this CAS, rather than publishing an obsolete revision.
    advanced={**draft,"revision":draft["revision"]+1,"lastPublicationAttemptAt":now,"lastPublicationAttemptBy":principal["id"]}
    claimed=await database[COLLECTION].replace_one({"_id":draft["_id"],"status":"DRAFT","revision":draft["revision"]},advanced)
    if not claimed.matched_count:raise APIError(409,"Draft changed while publishing; reload and review it")
    release["parentDraftRevision"]=draft["revision"]
    try:await database[COLLECTION].insert_one(release)
    except DuplicateKeyError as exc:raise APIError(409,"This policy version was already published") from exc
    return JSONResponse(status_code=201,content={"profile":public_document(release),"historicalApplicationsChanged":False})


@router.post("/applications/{application_id}/reassess")
async def reassess_application(application_id:str,payload:dict[str,Any],database:AsyncDatabase=Depends(get_mongo_database),principal:dict[str,str]=Depends(require_roles("ADMIN","STAFF"))):
    application=await database["visaapplications"].find_one(id_query(application_id))
    if not application:raise APIError(404,"Application not found")
    reason=str(payload.get("reason") or "").strip()
    if len(reason)<5:raise APIError(400,"A reassessment reason is required")
    chosen=str(payload.get("policyVersionId") or "")
    if not chosen:raise APIError(400,"Choose an explicit published policy version")
    profile=await load_published_profile(database,application.get("visaTypeId"),profile_id=chosen)
    if not profile:raise APIError(400,"Choose an effective published profile for this visa type")
    docs=await database["documents"].find(reference_query("applicationId",str(application["_id"]))).to_list(length=None)
    docs=[doc for doc in docs if str(doc.get("applicationId"))==str(application["_id"])]
    snapshot=profile_snapshot(profile)
    previous=deepcopy(application.get("evidencePolicySnapshot"))
    previous_assessment=deepcopy(application.get("evidenceAssessment"))
    previous_origin="PERSISTED_ASSESSMENT" if isinstance(previous_assessment,dict) else "UNAVAILABLE"
    previous_note="Preserved saved assessment; figures and evidence references reflect its saved calculation."
    if not isinstance(previous_assessment,dict):
        previous_assessment=None
        if isinstance(previous,dict) and previous.get("scoringVersion")==VERSION:
            previous_assessment=calculate_evidence_assessment(previous.get("requirements") or [],docs,policy=previous,application=application)
            previous_origin="RECONSTRUCTED_CURRENT_EVIDENCE"
            previous_note="No saved prior assessment existed. Reconstructed using the previous pinned policy and current evidence; this is not a historical result."
        else:
            previous_note="No saved prior assessment or pinned evidence policy is available; historical points cannot be established."
    updated={**application,"evidencePolicySnapshot":snapshot,"policyVersionId":snapshot["id"],"requirementSnapshot":deepcopy(snapshot["requirements"]),"scoringVersion":VERSION}
    assessment=calculate_evidence_assessment(snapshot["requirements"],docs,policy=snapshot,application=updated)
    now=datetime.now(timezone.utc)
    changes={"evidencePolicySnapshot":snapshot,"policyVersionId":snapshot["id"],"requirementSnapshot":deepcopy(snapshot["requirements"]),"scoringVersion":VERSION,"evidenceAssessment":assessment,"assessmentInputFingerprint":assessment.get("inputFingerprint"),"assessmentStatus":assessment["status"],"assessmentStale":False,"approvalProbability":None,"approvalProbabilityNormalized":None,"rejectionProbabilityNormalized":None,"predictionStatus":"STALE","verificationMetricsCurrent":False,"updatedAt":now}
    history_entry={"previousPolicyVersionId":(previous or {}).get("id"),"previousPolicySnapshot":previous,"previousAssessment":previous_assessment,"previousAssessmentOrigin":previous_origin,"previousAssessmentNote":previous_note,"policyVersionId":snapshot["id"],"assessment":deepcopy(assessment),"reason":reason,"reviewedBy":principal["id"],"createdAt":now}
    saved=await database["visaapplications"].update_one({"_id":application["_id"],"updatedAt":application.get("updatedAt")},{"$set":changes,"$push":{"assessmentHistory":history_entry}})
    if not saved.matched_count:raise APIError(409,"Application changed while reassessing; reload the case")
    return {"assessment":public_document(assessment),"policyVersionId":snapshot["id"],"probabilityInvalidated":True}


@router.post("/applications/{application_id}/review-requirement")
async def review_requirement(application_id:str,payload:dict[str,Any],database:AsyncDatabase=Depends(get_mongo_database),principal:dict[str,str]=Depends(require_roles("ADMIN","STAFF"))):
    """Record a rubric judgment/primary choice; this never silently verifies a document."""
    from ..services.evidence_scoring import _manual_quality
    application=await database["visaapplications"].find_one(id_query(application_id))
    if not application:raise APIError(404,"Application not found")
    snapshot=application.get("evidencePolicySnapshot")
    if not snapshot:raise APIError(409,"Reassess against a published evidence profile first")
    slot_id=str(payload.get("slotId") or "")
    requirements=[r for r in snapshot.get("requirements",[]) if str(r.get("alternativeGroupId") or r.get("requirementId"))==slot_id]
    if not requirements:raise APIError(400,"Requirement slot does not belong to this policy snapshot")
    document=await database["documents"].find_one(id_query(str(payload.get("documentId") or "")))
    if not document or str(document.get("applicationId"))!=str(application["_id"]):raise APIError(404,"Application document not found")
    requirement=next((r for r in requirements if str(r.get("requirementId"))==str(document.get("requirementId") or document.get("requirement"))),None)
    if not requirement:raise APIError(400,"Document does not satisfy this requirement slot")
    if str(document.get("status","")).upper() in {"DELETED","REPLACED"}:raise APIError(409,"Choose a current document revision")
    if requirement.get("qualityScorer")=="ABA_STATEMENT_V1":raise APIError(409,"Use the bank source-review workflow for bank ownership, verification and selection")
    reason=str(payload.get("reason") or "").strip()
    if len(reason)<5:raise APIError(400,"A review reason and source evidence are required")
    now=datetime.now(timezone.utc)
    review={"rubricVersion":(requirement.get("qualityRubric") or {}).get("version"),"reviewedBy":principal["id"],"reason":reason,"reviewedAt":now}
    if requirement.get("qualityScorer")=="MANUAL_RUBRIC":
        review.update({key:deepcopy(payload[key]) for key in ("criterionScores","levelId") if key in payload})
        quality,error=_manual_quality(requirement,{"qualityAssessment":review})
        if error:raise APIError(400,error)
        review["quality"]=str(quality)
        revision_query={"_id":document["_id"],"updatedAt":document.get("updatedAt"),"status":document.get("status")}
        changed=await database["documents"].update_one(revision_query,{"$set":{"qualityAssessment":review,"updatedAt":now},"$push":{"qualityAssessmentHistory":review}})
        if not changed.matched_count:raise APIError(409,"Document changed while reviewing; reload its source evidence")
        document={**document,"qualityAssessment":review,"updatedAt":now}
    choices={**(application.get("primaryDocumentSelections") or {}),slot_id:str(document["_id"])}
    docs=await database["documents"].find(reference_query("applicationId",str(application["_id"]))).to_list(length=None)
    docs=[document if str(d.get("_id"))==str(document["_id"]) else d for d in docs if str(d.get("applicationId"))==str(application["_id"])]
    updated={**application,"primaryDocumentSelections":choices}
    assessment=calculate_evidence_assessment(snapshot["requirements"],docs,policy=snapshot,application=updated)
    saved=await database["visaapplications"].update_one({"_id":application["_id"],"updatedAt":application.get("updatedAt")},{"$set":{"primaryDocumentSelections":choices,"evidenceAssessment":assessment,"assessmentInputFingerprint":assessment.get("inputFingerprint"),"assessmentStatus":assessment["status"],"assessmentStale":False,"approvalProbability":None,"approvalProbabilityNormalized":None,"rejectionProbabilityNormalized":None,"predictionStatus":"STALE","verificationMetricsCurrent":False,"updatedAt":now},"$push":{"assessmentHistory":{"policyVersionId":snapshot.get("id"),"assessment":assessment,"reason":reason,"reviewedBy":principal["id"],"createdAt":now}}})
    if not saved.matched_count:raise APIError(409,"Application changed while saving the review; reload the case")
    return {"assessment":public_document(assessment),"document":public_document(document),"probabilityInvalidated":True}
