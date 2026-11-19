import asyncio
import json
from types import SimpleNamespace
import bcrypt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError
from backend.app.api import first_run, assessment_exports
from backend.app.api.dependencies import get_mongo_database, get_current_principal
from backend.app.core.errors import APIError

class Collection:
    def __init__(self):self.rows=[]
    async def count_documents(self,q):return len(self.rows)
    async def insert_one(self,row):
        if any(r['_id']==row['_id'] for r in self.rows):raise DuplicateKeyError('duplicate')
        self.rows.append(row.copy());return SimpleNamespace(inserted_id=row['_id'])
    async def delete_one(self,q):self.rows=[r for r in self.rows if not all(r.get(k)==v for k,v in q.items())]
class DB(dict):
    def __missing__(self,key):self[key]=Collection();return self[key]

def setup_client(monkeypatch,host='127.0.0.1'):
    monkeypatch.setenv('LOCAL_SETUP_ENABLED','true')
    app=FastAPI();db=DB();app.include_router(first_run.router,prefix='/api/setup');app.dependency_overrides[get_mongo_database]=lambda:db
    @app.exception_handler(APIError)
    async def handle(request,exc):return JSONResponse(status_code=exc.status_code,content={'message':exc.message})
    return TestClient(app,base_url=f'http://{host}',client=('127.0.0.1',12345)),db

def test_first_admin_setup_once_and_password_hash(monkeypatch):
    client,db=setup_client(monkeypatch)
    assert client.get('/api/setup/status').json()['requiresSetup']
    payload={'name':'Synthetic Admin','email':'synthetic@example.test','password':'synthetic-test-password-123'}
    result=client.post('/api/setup/initialize',json=payload)
    assert result.status_code==201
    assert result.json()['user']['role']=='ADMIN'
    assert 'passwordHash' not in result.json()['user']
    stored=db['users'].rows[0]
    assert stored['passwordHash']!=payload['password']
    assert bcrypt.checkpw(payload['password'].encode(),stored['passwordHash'].encode())
    assert not client.get('/api/setup/status').json()['requiresSetup']
    assert client.post('/api/setup/initialize',json=payload).status_code==409

def test_setup_blocks_external_origins_and_dns_rebinding(monkeypatch):
    client,db=setup_client(monkeypatch)
    payload={'name':'Test','email':'test@example.test','password':'synthetic-test-password'}
    assert client.post('/api/setup/initialize',json=payload,headers={'Origin':'https://attacker.example'}).status_code==403
    other,_=setup_client(monkeypatch,'attacker.example')
    assert not other.get('/api/setup/status').json()['requiresSetup']
    assert other.post('/api/setup/initialize',json=payload,headers={'Origin':'http://attacker.example'}).status_code==404
    assert not db['users'].rows

def test_setup_disabled_and_invalid_password(monkeypatch):
    client,db=setup_client(monkeypatch)
    assert client.post('/api/setup/initialize',json={'name':'Test','email':'test@example.test','password':'weak'}).status_code==400
    monkeypatch.setenv('LOCAL_SETUP_ENABLED','false')
    assert not client.get('/api/setup/status').json()['requiresSetup']
    assert client.post('/api/setup/initialize',json={}).status_code==404

def test_exports_reuse_canonical_values_escape_html_and_csv(monkeypatch):
    app=FastAPI();app.include_router(assessment_exports.router,prefix='/api/assessments')
    app.dependency_overrides[get_mongo_database]=lambda:DB()
    app.dependency_overrides[get_current_principal]=lambda:{'id':'staff','email':'staff@example.test','role':'STAFF'}
    async def result(application_id,database,principal):
        assert principal['role']=='STAFF'
        return JSONResponse(content={'application':{'applicationNumber':'<script>BAD</script>'},'applicant':{'fullName':'Synthetic'},'summary':{'scoringVersion':'evidence-v3','confirmedReadinessPoints':15,'policyVersionId':'policy-1','requirementScores':[{'name':'=1+1','earnedPoints':15,'exactEarnedPoints':'15','configuredPoints':30,'quality':'0.5','scoringMode':'DYNAMIC','status':'VERIFIED'}]},'prediction':{'status':'UNAVAILABLE','approvalProbabilityNormalized':None}})
    monkeypatch.setattr(assessment_exports,'get_verification_results',result)
    with TestClient(app) as client:
        j=client.get('/api/assessments/case/exports/json');assert j.json()['summary']['confirmedReadinessPoints']==15
        c=client.get('/api/assessments/case/exports/csv');assert "'=1+1" in c.text
        h=client.get('/api/assessments/case/exports/html');assert '<script>BAD</script>' not in h.text and '&lt;script&gt;' in h.text
        assert h.headers['cache-control']=='no-store'


def test_aggregate_reports_and_dashboard_are_staff_only():
    from backend.app.main import app as business_app
    from backend.app.api import dependencies
    business_app.dependency_overrides[get_current_principal]=lambda:{"id":"customer-a","email":"a@example.test","role":"CUSTOMER"}
    try:
        with TestClient(business_app) as client:
            assert client.get('/api/dashboard').status_code==403
            assert client.get('/api/reports/statistics').status_code==403
            assert client.get('/api/customers').status_code==403
    finally:
        business_app.dependency_overrides.pop(get_current_principal,None)
