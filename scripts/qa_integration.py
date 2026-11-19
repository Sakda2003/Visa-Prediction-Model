"""Opt-in real-Mongo integration smoke test. Refuses to seed a non-QA database."""
from datetime import date, timedelta
import json
from pathlib import Path
import secrets
import sys
import time
import fitz
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.tests.aba_pdf_fixture import example_pdf

def run(base="http://127.0.0.1:8004"):
    client=httpx.Client(base_url=base,timeout=60)
    health=client.get('/api/system-health').json()
    assert str(health['database']['name']).endswith('_qa'),'Refusing to add test data outside an explicit QA database'
    def call(method,url,**kwargs):
        response=client.request(method,'/api'+url,**kwargs)
        if response.status_code>=400:raise AssertionError(f'{method} {url}: {response.status_code} {response.text[:600]}')
        return response.json()
    session_file=ROOT/'.runtime/qa-session.json'
    if session_file.exists():
        saved=json.loads(session_file.read_text());client.headers['Authorization']='Bearer '+saved['token']
        if client.get('/api/auth/me').status_code==200 and saved.get('applicationId'):
            print('Existing synthetic QA case available. Use --fresh after resetting the dedicated QA database.');return saved
    password=secrets.token_urlsafe(24)
    account=call('POST','/setup/initialize',json={'name':'Synthetic QA Administrator','email':'qa-admin@example.test','password':password})
    token=account['access_token'];client.headers['Authorization']='Bearer '+token
    saved={'baseUrl':base,'email':'qa-admin@example.test','password':password,'token':token}
    session_file.parent.mkdir(exist_ok=True);session_file.write_text(json.dumps(saved))
    country=call('POST','/countries',json={'name':'Synthetic QA destination','isoCode':'XZ','nationality':'XZZ'})['country']
    visa=call('POST','/visa-types',json={'name':'Synthetic visitor evidence profile','countryId':country['_id'],'category':'Tourist','entryType':'Single','validity':'90 Days','allowedStayDays':15,'visaClass':'S','applicantNationality':'KHM'})['visaType']
    requirements=[]
    for name,code,maximum,mode,scorer in [('Supporting identity evidence','identity_letter',70,'FIXED','FIXED'),('ABA bank statement','bank_statement',30,'DYNAMIC','ABA_STATEMENT_V1')]:
        response=call('POST','/requirements',json={'visaTypeId':visa['_id'],'name':name,'code':code,'weight':maximum,'required':True,'conditional':False,'acceptedFormats':['pdf'],'maxFileSize':10485760,'allowMultiple':False,'scoringMode':mode,'maximumEarnedPoints':str(maximum),'minimumEarnedPoints':'0','qualityScorer':scorer,'qualityScorerVersion':'1.0','mappingVersion':'bank-linear-1-to-10-v1' if mode=='DYNAMIC' else None,'requiredForFinalAssessment':True,'sourceKind':'INTERNAL'})
        requirements.append(response['requirement'])
    profile=call('POST','/policy-profiles',json={'visaTypeId':visa['_id'],'name':'Synthetic QA internal policy','version':'qa-1','timezone':'Asia/Phnom_Penh'})['profile']
    profile=call('POST',f'/policy-profiles/{profile["_id"]}/publish',json={'revision':profile['revision'],'reason':'Synthetic automated QA only; not official country guidance'})['profile']
    customer=call('POST','/customers',json={'fullName':'SYNTHETIC EXAMPLE','nationality':'Test nationality','passportNumber':'TEST000000','dateOfBirth':'1990-01-01','allowIncomplete':True})['customer']
    app=call('POST','/visa-types/applications',json={'customer':customer['_id'],'visaType':visa['_id'],'travelStartDate':(date.today()+timedelta(days=30)).isoformat(),'returnDate':(date.today()+timedelta(days=40)).isoformat()})['application']
    saved.update(applicationId=app['_id'],profileId=profile['_id'],visaTypeId=visa['_id']);session_file.write_text(json.dumps(saved))
    doc=fitz.open();page=doc.new_page();page.insert_text((60,70),'SYNTHETIC IDENTITY EVIDENCE - NOT A REAL DOCUMENT',fontsize=14);page.insert_text((60,110),'Name: SYNTHETIC EXAMPLE\nReference: TEST000000\nThis file exists only to test local OCR and reviewed fixed points.',fontsize=11)
    identity=doc.tobytes();doc.close()
    files=[('Synthetic Identity.pdf',identity,requirements[0]),('Synthetic ABA.pdf',example_pdf(rows=[]),requirements[1])]
    ids=[]
    for filename,content,requirement in files:
        uploaded=call('POST','/documents/upload',files={'file':(filename,content,'application/pdf')},data={'applicationId':app['_id'],'requirementId':requirement['_id']})['document'];ids.append(uploaded['_id'])
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            progress=call('GET',f'/documents/{uploaded["_id"]}/processing')
            if progress['ocrStatus'] in {'OCR_FAILED','OCR_PARTIAL'}:raise AssertionError(f'OCR stopped: {progress.get("error") or progress.get("message")}')
            if progress['ocrStatus']=='OCR_COMPLETED':break
            time.sleep(.15)
        else:raise AssertionError('OCR did not finish')
    call('PATCH',f'/documents/{ids[0]}/review',json={'status':'VERIFIED','reason':'Synthetic fixture inspected for fixed-point QA; no real identity assertion'})
    aba=call('GET',f'/bank-statements/documents/{ids[1]}/aba')['analysis']
    assert aba['statementStrengthScore']==5.5
    review=call('POST',f'/bank-statements/documents/{ids[1]}/aba/review',json={'analysisRevision':aba['analysisRevision'],'status':'VERIFIED','ownerRelationship':'APPLICANT','ownerRelationshipConfirmed':True,'reason':'Synthetic account and exact arithmetic reviewed for QA only'})['analysis']
    call('POST',f'/bank-statements/documents/{ids[1]}/aba/select-primary',json={'analysisRevision':review['analysisRevision'],'reason':'Explicitly selecting the reviewed synthetic primary statement'})
    call('POST','/verification/run',json={'applicationId':app['_id']})
    report=call('GET',f'/verification/results/{app["_id"]}')
    assert report['summary']['scoringVersion']=='evidence-v3'
    assert report['summary']['confirmedReadinessPoints']==85,report['summary']
    assert report['prediction']['approvalProbabilityNormalized'] is None
    for kind in ['json','csv','html']:
        r=client.get(f'/api/assessments/{app["_id"]}/exports/{kind}');assert r.status_code==200,(kind,r.text[:200])
        assert r.headers['cache-control']=='no-store'
    saved.update(documentIds=ids,expectedEvidencePoints=85);session_file.write_text(json.dumps(saved))
    print('Real Mongo/OCR/API smoke test passed: immutable profile; both local document paths; reviewed bank5.5 =>15/30; total85/100; no fake probability; protected exports.')
    return saved

if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8004')
