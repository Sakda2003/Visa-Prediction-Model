"""Synthetic integration tests; no real applicants or private PDFs are loaded."""
import asyncio
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.api import bank_statements as banks, documents as docs
from backend.app.api.dependencies import get_current_principal, get_mongo_database
from backend.app.core.errors import APIError
from backend.app.services.aba_integration import (
    aba_to_legacy_ocr, analyze_aba_pdf, apply_correction, normalized_bank_quality, reviewed_analysis,
)
from .aba_pdf_fixture import example_pdf


def nested(record, key):
    for name in key.split('.'):
        if not isinstance(record, dict) or name not in record:
            return None
        record = record[name]
    return record


class Collection:
    def __init__(self): self.rows = []; self.updates = []
    def matches(self, row, query):
        for key, wanted in query.items():
            if key == '$or':
                if not any(self.matches(row, branch) for branch in wanted): return False
                continue
            if key == '$and':
                if not all(self.matches(row, branch) for branch in wanted): return False
                continue
            actual = nested(row, key)
            if isinstance(wanted, dict):
                for operator, value in wanted.items():
                    if operator == '$in' and actual not in value: return False
                    if operator == '$nin' and actual in value: return False
                    if operator == '$ne' and actual == value: return False
                    if operator == '$exists' and (actual is not None) != value: return False
                    if operator == '$lte' and (actual is None or actual > value): return False
            elif actual != wanted: return False
        return True
    def find(self, query):
        async def to_list(length=None): return deepcopy([r for r in self.rows if self.matches(r, query)])
        return SimpleNamespace(to_list=to_list)
    async def find_one(self, query): return next((deepcopy(r) for r in self.rows if self.matches(r, query)), None)
    async def insert_one(self, row): self.rows.append(deepcopy(row))
    async def replace_one(self, query, replacement):
        for index, row in enumerate(self.rows):
            if self.matches(row, query): self.rows[index] = deepcopy(replacement); return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)
    async def update_one(self, query, changes):
        self.updates.append(deepcopy(changes))
        for row in self.rows:
            if not self.matches(row, query): continue
            for key, value in changes.get('$set', {}).items():
                target = row
                parts = key.split('.')
                for part in parts[:-1]: target = target.setdefault(part, {})
                target[parts[-1]] = deepcopy(value)
            for key in changes.get('$unset', {}): row.pop(key, None)
            for key, value in changes.get('$push', {}).items(): row.setdefault(key, []).append(deepcopy(value))
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class Database(dict):
    def __missing__(self, key): self[key] = Collection(); return self[key]


@pytest.fixture
def context(monkeypatch, tmp_path):
    db = Database(); scheduled = []
    requirement = {'_id':'bank','requirementId':'bank','visaTypeId':'visa','name':'Bank statement','code':'bank_statement',
        'status':'ACTIVE','required':True,'requiredForFinalAssessment':True,'weight':100,'scoringMode':'DYNAMIC',
        'minimumEarnedPoints':'0','maximumEarnedPoints':'100','qualityScorer':'ABA_STATEMENT_V1',
        'qualityScorerVersion':'1.0','mappingVersion':'bank-linear-1-to-10-v1',
        'acceptedFormats':['pdf'],'allowMultiple':False,'maxFileSize':10*1024*1024}
    snapshot={'id':'policy-1','version':'1','scoringVersion':'evidence-v3','requirements':[deepcopy(requirement)]}
    application={'_id':'application','visaTypeId':'visa','customer':'owner','requirementSnapshot':[deepcopy(requirement)],
        'evidencePolicySnapshot':snapshot,'policyVersionId':'policy-1','scoringVersion':'evidence-v3',
        'predictionStatus':'COMPLETED','approvalProbability':.8}
    db['visarequirements'].rows.append(deepcopy(requirement));db['visaapplications'].rows.append(application)
    monkeypatch.setattr(banks,'_PRIVATE_DIR',tmp_path);monkeypatch.setattr(docs,'_PRIVATE_DIR',tmp_path)
    monkeypatch.setattr(docs,'_schedule_document_ocr',lambda document,database,attempt: scheduled.append((deepcopy(document),attempt)) or True)
    def forbid_external(*args,**kwargs): raise AssertionError('Native ABA document must not call external OCR')
    monkeypatch.setattr(docs.httpx,'AsyncClient',forbid_external)
    app=FastAPI();app.include_router(banks.router,prefix='/api/bank-statements');app.include_router(docs.router,prefix='/api/documents')
    app.dependency_overrides[get_mongo_database]=lambda:db
    async def principal(request:Request):
        token=request.headers.get('authorization','')
        if not token.startswith('Bearer '):raise APIError(401,'Authentication required')
        role=token[7:]
        if role not in {'STAFF','ADMIN','CUSTOMER'}:raise APIError(401,'Invalid role')
        return {'id':'owner' if role=='CUSTOMER' else 'reviewer','email':'synthetic@example.test','role':role}
    app.dependency_overrides[get_current_principal]=principal
    @app.exception_handler(APIError)
    async def error(_request,error): return JSONResponse(status_code=error.status_code,content={'message':error.message})
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,db=db,scheduled=scheduled,tmp=tmp_path,headers={'Authorization':'Bearer STAFF'})
    docs._PDF_PASSWORDS.clear()


def upload_and_process(c, raw=None, password=None):
    raw=raw or example_pdf(rows=[])
    data={'applicationId':'application','requirementId':'bank'}
    if password is not None:data['pdfPassword']=password
    response=c.client.post('/api/documents/upload',headers=c.headers,data=data,files={'file':('Synthetic.pdf',raw,'application/pdf')})
    assert response.status_code==201,response.text
    document_id=response.json()['document']['_id']
    document,attempt=c.scheduled[-1]
    asyncio.run(docs._process_document_ocr(document,c.db,attempt))
    document=asyncio.run(c.db['documents'].find_one({'_id':document_id}))
    return document


def review(c, document, **overrides):
    analysis=document['abaStatementAnalysis']
    payload={'analysisRevision':analysis['analysisRevision'],'status':'VERIFIED','reason':'Checked synthetic source and owner.',
             'ownerRelationship':'APPLICANT','ownerRelationshipConfirmed':True,**overrides}
    return c.client.post(f'/api/bank-statements/documents/{document["_id"]}/aba/review',headers=c.headers,json=payload)


def test_bank_normalization_and_major_unit_boundary():
    a=analyze_aba_pdf(example_pdf(rows=[]),'Synthetic.pdf')
    assert a['statementStrengthScore']==5.5
    assert normalized_bank_quality(5.5)=='0.5'
    assert normalized_bank_quality(None) is None
    legacy=aba_to_legacy_ocr(a)
    assert a['metrics']['opening']==450000 and legacy['fields']['openingBalance']==4500
    assert legacy['fields']['derivedMonthlyBalances'][0]['evidenceType']=='DERIVED_DAILY_CLOSING'
    assert legacy['confidence'] is None


def test_standalone_and_application_upload_share_local_reader(context):
    c=context;raw=example_pdf(rows=[])
    standalone=c.client.post('/api/bank-statements/analyze',headers=c.headers,files={'file':('Synthetic.pdf',raw,'application/pdf')})
    assert standalone.status_code==201,standalone.text
    assert standalone.json()['result']['score']['ocr_confidence'] is None
    doc=upload_and_process(c,raw)
    assert doc['abaStatementAnalysis']['statementStrengthScore']==standalone.json()['abaStatementAnalysis']['statementStrengthScore']==5.5
    assert doc['status']=='NEEDS_REVIEW' and doc['awardedPoints']==0
    assert doc['ocrConfidence'] is None
    assert any('Reading ABA page 1 of 1' in u.get('$set',{}).get('ocrMessage','') for u in c.db['documents'].updates)
    app=c.db['visaapplications'].rows[0]
    assert app['scoringVersion']=='evidence-v3'
    assert app['evidenceAssessment']['confirmedReadinessPoints']==0
    assert app['approvalProbability'] is None


def test_review_and_explicit_revisioned_primary_selection(context):
    c=context;doc=upload_and_process(c)
    response=review(c,doc);assert response.status_code==200,response.text
    reviewed=response.json()['analysis']
    assert reviewed['reviewStatus']=='VERIFIED'
    assert c.db['visaapplications'].rows[0]['evidenceAssessment']['confirmedReadinessPoints']==0
    selected=c.client.post(f'/api/bank-statements/documents/{doc["_id"]}/aba/select-primary',headers=c.headers,
        json={'analysisRevision':reviewed['analysisRevision'],'reason':'Selected applicant primary statement.'})
    assert selected.status_code==200,selected.text
    assert selected.json()['selectedPrimary'] is True
    app=c.db['visaapplications'].rows[0]
    assert app['selectedBankAnalysisRevision']==reviewed['analysisRevision']
    assert app['evidenceAssessment']['confirmedReadinessPoints']==50
    assert app['evidenceAssessment']['maximumPoints']==100
    # Re-review rotates the analysis revision, so a previously selected account
    # must expose the primary action again rather than leaving the UI disabled.
    current=asyncio.run(c.db['documents'].find_one({'_id':doc['_id']}))
    rerun=review(c,current)
    assert rerun.status_code==200 and rerun.json()['selectedPrimary'] is False
    assert c.db['visaapplications'].rows[0]['evidenceAssessment']['confirmedReadinessPoints']==0


def test_source_preview_and_customer_authorization(context):
    c=context;doc=upload_and_process(c);url=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    assert c.client.get(url).status_code==401
    assert c.client.get(url,headers={'Authorization':'Bearer CUSTOMER'}).status_code==403
    response=c.client.get(url+'/page/1',headers=c.headers)
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert response.headers['cache-control']=='no-store'
    assert c.client.get(url+'/page/2',headers=c.headers).status_code==404


def test_stale_review_cannot_overwrite_new_analysis(context):
    c=context;raw=example_pdf(rows=[(date(2026,1,2),10000,0,'Synthetic credit REF# ONE')]);doc=upload_and_process(c,raw)
    old=doc['abaStatementAnalysis']['analysisRevision'];base=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    changed=c.client.post(base+'/corrections',headers=c.headers,json={'analysisRevision':old,'row':1,'field':'balance','value':'4599.91','reason':'Synthetic inconsistency for review test.','sourceResolutionConfirmed':True})
    assert changed.status_code==200,changed.text
    analysis=changed.json()['analysis']
    assert analysis['statementStrengthScore'] is None
    assert any(abs(i.get('difference',0))==9 for i in analysis['issues'])
    rejected=review(c,doc)
    assert rejected.status_code==409
    fresh=deepcopy(doc);fresh['abaStatementAnalysis']=analysis
    blocked=review(c,fresh)
    assert blocked.status_code==409 and 'reconciliation' in blocked.json()['message'].lower()
    persisted=c.db['documents'].rows[0]
    assert persisted['extractedFields']['transactionHistory'][0]['balance']==4599.91


def test_documented_correction_resolution_never_waives_arithmetic(context):
    c=context;doc=upload_and_process(c,example_pdf(rows=[(date(2026,1,2),10000,0,'Synthetic credit REF# ONE')]))
    base=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    a=doc['abaStatementAnalysis']
    changed=c.client.post(base+'/corrections',headers=c.headers,json={'analysisRevision':a['analysisRevision'],'row':1,'field':'balance','value':'4599.91','reason':'Synthetic extraction correction.'}).json()['analysis']
    assert any(i['code']=='correction_requires_resolution' for i in changed['issues'])
    payload={'analysisRevision':changed['analysisRevision'],'status':'VERIFIED','reason':'Confirmed source resolution only.','ownerRelationship':'APPLICANT','ownerRelationshipConfirmed':True,'resolveCorrections':True,'sourceResolutionConfirmed':True}
    assert c.client.post(base+'/review',headers=c.headers,json=payload).status_code==409


def test_password_is_consumed_and_encrypted_preview_is_private(context):
    c=context
    source=fitz.open(stream=example_pdf(rows=[]),filetype='pdf')
    raw=source.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='synthetic-password')
    doc=upload_and_process(c,raw,password='synthetic-password')
    assert doc['_id'] not in docs._PDF_PASSWORDS
    assert doc['abaStatementAnalysis']['reconciliationStatus']=='RECONCILED'
    assert c.client.get(f'/api/bank-statements/documents/{doc["_id"]}/aba/page/1',headers=c.headers).status_code==200
    assert 'synthetic-password' not in repr(c.db['documents'].rows)


def test_missing_password_can_be_retried_without_reuploading(context):
    c=context
    source=fitz.open(stream=example_pdf(rows=[]),filetype='pdf')
    raw=source.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='synthetic-password')
    doc=upload_and_process(c,raw)
    assert doc['status']=='FAILED' and doc['ocrStatus']=='OCR_FAILED'
    assert doc['abaStatementAnalysis']['statementStrengthScore'] is None
    response=c.client.post(f'/api/documents/{doc["_id"]}/ocr/retry',headers=c.headers,json={'pdfPassword':'synthetic-password'})
    assert response.status_code==200,response.text
    retry,attempt=c.scheduled[-1]
    asyncio.run(docs._process_document_ocr(retry,c.db,attempt))
    fresh=asyncio.run(c.db['documents'].find_one({'_id':doc['_id']}))
    assert fresh['ocrStatus']=='OCR_COMPLETED'
    assert fresh['abaStatementAnalysis']['statementStrengthScore']==5.5
    assert doc['_id'] not in docs._PDF_PASSWORDS


def test_late_job_does_not_resurrect_deleted_document(context):
    c=context;doc=upload_and_process(c)
    row=c.db['documents'].rows[0];row['status']='DELETED';row['ocrLeaseOwner']='new-worker'
    current=deepcopy(row)
    analysis=analyze_aba_pdf(example_pdf(rows=[]),'Synthetic.pdf')
    asyncio.run(docs._complete_local_aba(doc,c.db,analysis,'new-hash','old-worker'))
    assert row==current


def test_saved_snapshot_controls_upload_after_live_requirement_changes(context):
    c=context
    c.db['visarequirements'].rows[0].update(acceptedFormats=['png'],maxFileSize=1,name='Changed live requirement')
    doc=upload_and_process(c)
    assert doc['requirementCode']=='bank_statement'
    assert doc['abaStatementAnalysis']['statementStrengthScore']==5.5
