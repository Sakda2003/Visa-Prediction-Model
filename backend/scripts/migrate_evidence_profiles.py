"""Opt-in draft-only migration. No historical scores, documents, or applications are rewritten."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.services.policy_profiles import content_hash, requirement_snapshot, validate_profile
from backend.app.services.evidence_scoring import validate_profile_requirements


def plan_drafts(visa_types, requirements, run_id):
    """Copy legacy configuration as unnormalized draft evidence; invalid budgets remain unpublished."""
    now=datetime.now(timezone.utc)
    drafts=[]
    for visa in visa_types:
        visa_id=str(visa.get("_id") or "")
        if not visa_id:continue
        rows=[r for r in requirements if str(r.get("visaTypeId"))==visa_id and str(r.get("status","ACTIVE")).upper()=="ACTIVE"]
        if not rows:continue
        reqs=[requirement_snapshot(r) for r in rows]
        draft={"_id":"migration-"+hashlib.sha256((run_id+":"+visa_id).encode()).hexdigest()[:32],"name":"Migrated legacy evidence draft","version":"migration-"+run_id,
            "visaTypeId":visa_id,"countryId":str(visa.get("countryId") or ""),"requirements":deepcopy(reqs),"timezone":"Asia/Phnom_Penh","status":"DRAFT","revision":1,
            "createdAt":now,"updatedAt":now,"createdBy":"migration:"+run_id,"migrationRunId":run_id,"migrationWarnings":[]}
        draft=validate_profile(draft)
        try:validate_profile_requirements(reqs)
        except (ValueError,TypeError) as exc:draft["migrationWarnings"].append(str(exc))
        draft["migrationSourceHash"]=content_hash(draft)
        drafts.append(draft)
    return drafts


def rollback_eligible(draft, run_id, published_parent_ids=()):
    return (draft.get("migrationRunId")==run_id and draft.get("status")=="DRAFT" and draft.get("revision")==1
        and draft.get("_id") not in set(published_parent_ids) and content_hash(draft)==draft.get("migrationSourceHash"))


async def migrate(database, run_id, *, apply=False):
    visas=await database["visatypes"].find({}).to_list(length=None)
    requirements=await database["visarequirements"].find({}).to_list(length=None)
    drafts=plan_drafts(visas,requirements,run_id)
    added=0
    for draft in drafts:
        if apply:
            from pymongo.errors import DuplicateKeyError
            try:
                await database["evidencepolicyprofiles"].insert_one(draft);added+=1
            except DuplicateKeyError:
                # Retrying the same run never edits its previous draft or forks another one.
                pass
    return {"runId":run_id,"apply":apply,"draftsPlanned":len(drafts),"draftsCreated":added,"draftsNeedingAdminRevision":sum(bool(d["migrationWarnings"]) for d in drafts),"historicalApplicationsChanged":0,"policiesPublished":0}


async def rollback(database, run_id, *, apply=False):
    rows=await database["evidencepolicyprofiles"].find({"migrationRunId":run_id}).to_list(length=None)
    published=await database["evidencepolicyprofiles"].find({"status":"PUBLISHED"}).to_list(length=None)
    parent_ids={p.get("parentDraftId") for p in published}
    eligible=[r for r in rows if rollback_eligible(r,run_id,parent_ids)]
    deleted=0
    if apply:
        for draft in eligible:
            # Fresh CAS ensures a concurrently edited/published draft is never removed.
            latest=await database["evidencepolicyprofiles"].find_one({"_id":draft["_id"]})
            if not latest or not rollback_eligible(latest,run_id,parent_ids):continue
            result=await database["evidencepolicyprofiles"].delete_one({"_id":draft["_id"],"status":"DRAFT","revision":1,"migrationRunId":run_id,"migrationSourceHash":draft["migrationSourceHash"]})
            deleted+=result.deleted_count
    return {"runId":run_id,"apply":apply,"eligibleDrafts":len(eligible),"refusedChangedOrPublishedDrafts":len(rows)-len(eligible),"draftsDeleted":deleted,"historicalApplicationsChanged":0}


async def main_async(args):
    from backend.app.database.mongodb import create_mongo_client
    from backend.app.core.config import settings
    client=create_mongo_client()
    try:
        db=client.get_database(settings.mongodb_database)
        await client.admin.command("ping")
        result=await rollback(db,args.rollback_run,apply=args.apply) if args.rollback_run else await migrate(db,args.run_id or str(uuid4()),apply=args.apply)
        print(json.dumps(result,indent=2))
    finally:await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true",help="Create migration drafts, or delete only unchanged migration drafts with --rollback-run. Default is read-only dry run.")
    parser.add_argument("--run-id",help="A stable identifier for idempotent draft creation.")
    parser.add_argument("--rollback-run",help="Plan a rollback of unchanged unpublished drafts from this migration run.")
    args=parser.parse_args()
    if args.run_id and args.rollback_run:parser.error("Choose migration or rollback, not both")
    asyncio.run(main_async(args))


if __name__=="__main__":main()
