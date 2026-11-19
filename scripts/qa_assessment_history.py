"""Exercise saved policy history and reviewed sponsor ownership in the isolated QA case."""
from copy import deepcopy
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run():
    session_file = ROOT / ".runtime" / "qa-session.json"
    session = json.loads(session_file.read_text())
    with httpx.Client(base_url=session["baseUrl"], headers={"Authorization": "Bearer " + session["token"]}, timeout=60) as client:
        health = client.get("/api/system-health").json()
        assert health["database"]["name"].endswith("_qa"), "Refusing to change any non-QA database"

        def call(method, url, **kwargs):
            response = client.request(method, "/api" + url, **kwargs)
            response.raise_for_status()
            return response.json()

        bank_id = session["documentIds"][1]
        bank_base = f"/bank-statements/documents/{bank_id}/aba"
        analysis = call("GET", bank_base)["analysis"]
        if analysis.get("ownerRelationship") != "SPONSOR":
            analysis = call("POST", bank_base + "/review", json={
                "analysisRevision": analysis["analysisRevision"], "status": "VERIFIED",
                "ownerRelationship": "SPONSOR", "ownerRelationshipConfirmed": True,
                "reason": "Synthetic sponsor relationship checked for local regression only; no real account assertion.",
            })["analysis"]
            call("POST", bank_base + "/select-primary", json={"analysisRevision": analysis["analysisRevision"], "reason": "Explicitly selecting this reviewed synthetic sponsor revision for QA."})
        assert call("GET", bank_base)["analysis"]["ownerRelationship"] == "SPONSOR"
        profiles = call("GET", "/policy-profiles", params={"visaTypeId": session["visaTypeId"]})["profiles"]
        published = next((p for p in profiles if p["version"] == "qa-history-3" and p["status"] == "PUBLISHED"), None)
        if published is None:
            original = next(p for p in profiles if p["version"] == "qa-1" and p["status"] == "PUBLISHED")
            requirements = deepcopy(original["requirements"])
            for requirement in requirements:
                maximum = 40 if requirement["qualityScorer"] == "ABA_STATEMENT_V1" else 60
                requirement.update(weight=maximum, maximumEarnedPoints=str(maximum), minimumEarnedPoints="0")
            draft = call("POST", "/policy-profiles", json={"visaTypeId": session["visaTypeId"], "name": "Synthetic history comparison policy", "version": "qa-history-3", "timezone": "Asia/Phnom_Penh", "requirements": requirements})["profile"]
            published = call("POST", f'/policy-profiles/{draft["_id"]}/publish', json={"revision": draft["revision"], "reason": "Synthetic QA-only 60/40 allocation; not official country guidance."})["profile"]
        application_id = session["applicationId"]
        result = call("GET", f"/verification/results/{application_id}")
        if result["application"].get("policyVersionId") != published["_id"]:
            assert result["summary"]["confirmedReadinessPoints"] == 85
            call("POST", f"/policy-profiles/applications/{application_id}/reassess", json={"policyVersionId": published["_id"], "reason": "Synthetic QA comparison: preserve the original 85-point result while applying an explicit 60/40 budget."})
        result = call("GET", f"/verification/results/{application_id}")
        assert result["summary"]["confirmedReadinessPoints"] == 80
        history = next(h for h in result["assessmentHistory"] if h["policyVersionId"] == published["_id"])
        assert history["previousAssessmentOrigin"] == "PERSISTED_ASSESSMENT"
        assert history["previousAssessment"]["confirmedReadinessPoints"] == 85
        assert history["assessment"]["confirmedReadinessPoints"] == 80
        assert result["prediction"]["approvalProbabilityNormalized"] is None
        session.update(expectedEvidencePoints=80, comparisonProfileId=published["_id"])
        session_file.write_text(json.dumps(session))
        print("History QA passed: persisted 85 -> 80 points; immutable old ledger; reviewed SPONSOR survives reload; probability unavailable.")


if __name__ == "__main__":
    run()
