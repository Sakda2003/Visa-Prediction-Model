"""Historical ledgers preserve saved figures and distinguish current-evidence reconstructions."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio

import pytest

from backend.app.api import policy_profiles
from backend.tests.test_evidence_profiles import Database, document, requirement
from backend.tests.test_entities import entity_client, _headers


def test_reassessment_preserves_saved_previous_ledger_and_full_policy_snapshot():
    async def scenario():
        db=Database()
        old={"id":"old","version":"1","name":"Old policy","scoringVersion":"evidence-v3","requirements":[requirement("passport",70),requirement("ties",30)]}
        saved_ledger={"confirmedReadinessPoints":70,"exactPoints":"70","lastCalculationAt":"2026-01-01T00:00:00Z","requirementScores":[{"requirementId":"passport","earnedPoints":70}]}
        await db["visaapplications"].insert_one({"_id":"application","visaTypeId":"visa","evidencePolicySnapshot":old,"evidenceAssessment":saved_ledger})
        await db["evidencepolicyprofiles"].insert_one({"_id":"new","version":"2","name":"New policy","visaTypeId":"visa","status":"PUBLISHED","scoringVersion":"evidence-v3","publishedAt":datetime(2026,1,1,tzinfo=timezone.utc),"requirements":[requirement("passport",100)]})
        # Current evidence would now give the old profile100; that must not rewrite its saved70.
        await db["documents"].insert_one(document("passport"))
        await db["documents"].insert_one(document("ties"))
        result=await policy_profiles.reassess_application("application",{"policyVersionId":"new","reason":"Reviewed new policy explicitly"},db,{"id":"staff"})
        app=await db["visaapplications"].find_one({"_id":"application"})
        history=app["assessmentHistory"][0]
        assert result["assessment"]["earnedPoints"]==100
        assert history["previousAssessment"]==saved_ledger
        assert history["previousPolicySnapshot"]==old
        assert history["previousAssessmentOrigin"]=="PERSISTED_ASSESSMENT"
        db["visaapplications"].rows[0]["evidenceAssessment"]["confirmedReadinessPoints"]=1
        db["visaapplications"].rows[0]["evidencePolicySnapshot"]["requirements"][0]["weight"]=1
        preserved=db["visaapplications"].rows[0]["assessmentHistory"][0]
        assert preserved["previousAssessment"]["confirmedReadinessPoints"]==70
        assert preserved["previousPolicySnapshot"]["requirements"][0]["weight"]==70
        assert preserved["assessment"]["confirmedReadinessPoints"]==100
    asyncio.run(scenario())


@pytest.mark.parametrize("has_pinned_policy",[True,False])
def test_missing_saved_assessment_is_explicitly_reconstructed_or_unavailable(has_pinned_policy):
    async def scenario():
        db=Database()
        old={"id":"old","version":"1","scoringVersion":"evidence-v3","requirements":[requirement("passport",70),requirement("ties",30)]}
        app={"_id":"application","visaTypeId":"visa"}
        if has_pinned_policy:app["evidencePolicySnapshot"]=old
        await db["visaapplications"].insert_one(app)
        await db["evidencepolicyprofiles"].insert_one({"_id":"new","version":"2","name":"New policy","visaTypeId":"visa","status":"PUBLISHED","scoringVersion":"evidence-v3","publishedAt":datetime(2026,1,1,tzinfo=timezone.utc),"requirements":[requirement("passport",100)]})
        await db["documents"].insert_one(document("passport"))
        await policy_profiles.reassess_application("application",{"policyVersionId":"new","reason":"Reviewed new policy explicitly"},db,{"id":"staff"})
        history=db["visaapplications"].rows[0]["assessmentHistory"][0]
        if has_pinned_policy:
            assert history["previousAssessmentOrigin"]=="RECONSTRUCTED_CURRENT_EVIDENCE"
            assert history["previousAssessment"]["confirmedReadinessPoints"]==70
            assert "not a historical result" in history["previousAssessmentNote"]
        else:
            assert history["previousAssessmentOrigin"]=="UNAVAILABLE"
            assert history["previousAssessment"] is None
            assert history["previousPolicySnapshot"] is None
    asyncio.run(scenario())


def test_history_report_access_is_case_scoped_and_redacted(entity_client):
    client,db=entity_client
    snapshot={"id":"policy","version":"1","name":"Synthetic","scoringVersion":"evidence-v3","requirements":[requirement("passport",100)]}
    original={"previousPolicyVersionId":"old","previousPolicySnapshot":snapshot,"previousAssessment":{"confirmedReadinessPoints":70,"accountNumber":"123456789","passportNumber":"AB1234567","storagePath":"private/location","password":"hidden"},"previousAssessmentOrigin":"PERSISTED_ASSESSMENT","assessment":{"confirmedReadinessPoints":100},"reason":"Reviewed account number: 123456789","reviewedBy":"staff","createdAt":"2026-01-01T00:00:00Z"}
    db["visaapplications"].documents.append({"_id":"history-case","customer":"test-user","visaTypeId":"visa","evidencePolicySnapshot":snapshot,"assessmentHistory":[deepcopy(original)]})
    db["customers"].documents.append({"_id":"test-user","fullName":"Synthetic applicant"})
    db["visatypes"].documents.append({"_id":"visa","destination":"Japan","name":"Visitor"})
    db["documents"].documents.append({**document("passport"),"applicationId":"history-case","extractedFields":{"accountNumber":"123456789"}})
    for role in ("STAFF","CUSTOMER"):
        response=client.get("/api/verification/results/history-case",headers=_headers(role))
        assert response.status_code==200,response.text
        entry=response.json()["assessmentHistory"][0]
        assert entry["previousAssessment"]["confirmedReadinessPoints"]==70
        assert entry["previousAssessment"]["accountNumber"]!="123456789"
        assert entry["previousAssessment"]["passportNumber"]!="AB1234567"
        assert "password" not in entry["previousAssessment"]
        assert "storagePath" not in entry["previousAssessment"]
        assert "123456789" not in entry["reason"]
    assert db["visaapplications"].documents[0]["assessmentHistory"][0]==original
    db["visaapplications"].documents[0]["customer"]="another-customer"
    assert client.get("/api/verification/results/history-case",headers=_headers("CUSTOMER")).status_code==404
