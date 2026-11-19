"""Synthetic model/assessment integration; no actual visa outcomes or private documents."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
from pathlib import Path

import pandas as pd
import pytest
from bson import BSON

from backend.app.api import verification_reports as reports
from backend.app.core.errors import APIError
from backend.app.services.assessment_context import (
    ABA_FEATURE_SCHEMA, canonical_summary, prediction_fingerprint, application_requirements, digest,
)
from backend.app.services.ml_training import train_model, predict_with_artifact, validate_training_dataset
from backend.tests.test_entities import entity_client, _headers


def _profile_case(database, *, complete=True):
    requirements=[{"requirementId":"identity","code":"passport","name":"Identity evidence","required":True,"scoringMode":"FIXED","maximumEarnedPoints":"100","minimumEarnedPoints":"0","weight":100}]
    snapshot={"id":"policy-case-v1","status":"PUBLISHED","scoringVersion":"evidence-v3","version":"1","name":"Synthetic policy","visaTypeId":"case-visa","countryId":"JP","timezone":"Asia/Phnom_Penh","requirements":requirements}
    app={"_id":"case","customer":"test-user","visaTypeId":"case-visa","destination":"Japan","category":"Tourist","policyVersionId":snapshot["id"],"evidencePolicySnapshot":snapshot,"requirementSnapshot":requirements,"status":"DRAFT","applicationNumber":"SYNTHETIC-CASE"}
    document={"_id":"identity-document","applicationId":"case","requirementId":"identity","requirementCode":"passport","fileName":"synthetic.png","status":"VERIFIED" if complete else "NEEDS_REVIEW","ocrStatus":"OCR_COMPLETED","ocrTotalPages":1,"ocrPagesProcessed":1,"ocrProgressPercent":100,"ocrPageNumbers":[1]}
    database["visaapplications"].documents.append(app)
    database["customers"].documents.append({"_id":"test-user","fullName":"Synthetic Applicant"})
    database["visatypes"].documents.append({"_id":"case-visa","name":"Visitor","destination":"Japan"})
    database["documents"].documents.append(document)
    return app,document


def test_profile_report_uses_snapshot_and_no_fabricated_prediction(entity_client):
    client,database=entity_client
    app,doc=_profile_case(database)
    database["visarequirements"].documents.append({"_id":"changed-live","visaTypeId":"case-visa","name":"Changed later","weight":1,"status":"ACTIVE"})
    response=client.get('/api/verification/results/case',headers=_headers('STAFF'))
    assert response.status_code==200
    body=response.json()
    assert body['summary']['confirmedReadinessPoints']==100
    assert body['summary']['maximumPoints']==100
    assert body['prediction']['approvalProbability'] is None
    assert body['prediction']['approvalProbabilityNormalized'] is None
    assert body['application']['policyVersionId']=='policy-case-v1'
    assert body['application']['visaTypeId']=='case-visa'
    assert body['requirements'][0]['requirementId']=='identity'
    assert application_requirements(app, database['visarequirements'].documents)[0]['requirementId']=='identity'


def test_completed_prediction_becomes_stale_after_selected_evidence_changes(entity_client):
    client,database=entity_client
    app,doc=_profile_case(database)
    # A stored probability without its original input fingerprint cannot be reused.
    app.update(predictionStatus='COMPLETED',approvalProbability=88,rejectionProbability=12,predictionModelVersion='old-model')
    response=client.get('/api/verification/results/case',headers=_headers('STAFF')).json()
    assert response['prediction']['status']=='STALE'
    assert response['prediction']['approvalProbability'] is None
    assert response['prediction']['approvalProbabilityNormalized'] is None
    first=prediction_fingerprint(app,{},app['requirementSnapshot'],[doc])
    changed={**app,'primaryDocumentSelections':{'identity':'a-different-document'}}
    assert prediction_fingerprint(changed,{},app['requirementSnapshot'],[doc])!=first
    assert prediction_fingerprint({**app,'predictionTimestamp':'now','predictionInProgress':True},{},app['requirementSnapshot'],[doc])==first


def test_profile_prediction_unavailable_without_model_and_browser_vectors_rejected(entity_client):
    client,database=entity_client
    _profile_case(database)
    response=client.post('/api/verification/case/predict',headers=_headers('STAFF'))
    assert response.status_code==200,response.text
    assert response.json()['prediction']['status']=='UNAVAILABLE'
    saved=database['visaapplications'].documents[0]
    assert saved['approvalProbability'] is None
    assert saved['approvalProbabilityNormalized'] is None
    raw=client.post('/api/ml/predict',headers=_headers('STAFF'),json={'model_type':'visa','features':{'financial_score':100}})
    assert raw.status_code==400


def test_evidence_only_finalization_without_model_and_stale_verification_gate(entity_client,monkeypatch,tmp_path):
    client,database=entity_client
    app,doc=_profile_case(database)
    monkeypatch.setattr(reports,'_RECEIPT_DIR',tmp_path)
    verified=client.post('/api/verification/run',json={'applicationId':'case'},headers=_headers('STAFF'))
    assert verified.status_code==200,verified.text
    prediction_report=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','reportType':'PREDICTION'},headers=_headers('STAFF'))
    assert prediction_report.status_code==409
    final=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','reportType':'EVIDENCE_ASSESSMENT','comments':'Reviewed synthetic source'},headers=_headers('STAFF'))
    assert final.status_code==200,final.text
    assert final.json()['report']['reportType']=='EVIDENCE_ASSESSMENT'
    assert final.json()['report']['prediction']['approvalProbability'] is None
    assert final.json()['report']['prediction']['approvalProbabilityNormalized'] is None
    assert list(tmp_path.glob('*.pdf'))


def test_profile_finalization_rejects_document_changed_since_verification(entity_client,monkeypatch,tmp_path):
    client,database=entity_client
    app,doc=_profile_case(database)
    monkeypatch.setattr(reports,'_RECEIPT_DIR',tmp_path)
    assert client.post('/api/verification/run',json={'applicationId':'case'},headers=_headers('STAFF')).status_code==200
    doc['revision']=2
    result=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','reportType':'EVIDENCE_ASSESSMENT'},headers=_headers('STAFF'))
    assert result.status_code==409
    assert 'changed since' in result.json()['message']
    assert not list(tmp_path.glob('*.pdf'))


def test_profile_finalization_rejects_document_changed_during_render(entity_client,monkeypatch,tmp_path):
    client,database=entity_client
    app,doc=_profile_case(database)
    monkeypatch.setattr(reports,'_RECEIPT_DIR',tmp_path)
    assert client.post('/api/verification/run',json={'applicationId':'case'},headers=_headers('STAFF')).status_code==200
    def render(*args):
        doc['revision']=99
        return b'%PDF-1.4 synthetic race fixture'
    monkeypatch.setattr(reports,'create_receipt_pdf',render)
    result=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','reportType':'EVIDENCE_ASSESSMENT'},headers=_headers('STAFF'))
    assert result.status_code==409
    assert 'while the report' in result.json()['message']
    assert not list(tmp_path.glob('*.pdf'))


def test_aba_features_preserve_units_and_legacy_financial_meaning():
    doc={'_id':'bank','requirementCode':'bank_statement','status':'VERIFIED','ocrStatus':'OCR_COMPLETED','ocrTotalPages':1,'ocrPagesProcessed':1,'ocrProgressPercent':100,'ocrPageNumbers':[1],
         'extractedFields':{'currency':'USD'},'abaStatementAnalysis':{'statementStrengthScore':7,'normalizedQuality':'0.666666666666666666','reviewStatus':'VERIFIED','reconciliationStatus':'RECONCILED','ownerRelationshipConfirmed':True,'source':{'currency':'USD'},'metrics':{'average':'450000','ending':700000,'money_in':300000,'money_out':50000,'days':90},'issues':[]}}
    app={'destination':'Japan','category':'Tourist','selectedBankDocumentId':'bank','evidencePolicySnapshot':{'requirements':[]}}
    features=reports._build_visa_features(app,{'nationality':'Cambodian','monthlyIncomeUsd':99999},[doc],{'confirmedReadinessPoints':78})
    assert features['_feature_schema_version']==ABA_FEATURE_SCHEMA
    assert features['aba_statement_strength']==7
    assert features['aba_average_daily_balance_usd']==4500
    assert features['aba_money_in_usd']==3000
    assert features.get('financial_score')!=7
    assert 'nationality' not in features
    assert 'monthly_income_usd' not in features
    app['verifiedPredictionInputs']={'reviewedBy':'staff','reason':'Checked source evidence','sourceReferences':['bank:page1'],'values':{'monthly_income_usd':1500}}
    verified=reports._build_visa_features(app,{'monthlyIncomeUsd':99999},[doc],{'confirmedReadinessPoints':78})
    assert verified['monthly_income_usd']==1500


def test_logistic_regression_uses_calibration_and_cohort_schema_gates():
    frame=pd.DataFrame([{'group_id':f'synthetic-person-{i}','visa_outcome':'APPROVED' if i%2==0 else 'REFUSED','destination_country':'Japan','visa_category':'Tourist','aba_statement_strength':8 if i%2==0 else 3,'aba_statement_quality':7/9 if i%2==0 else 2/9,'evidence_strength_score':85 if i%2==0 else 35} for i in range(500)])
    artifact,metadata=train_model('visa',frame,b'synthetic test-only outcomes',algorithm='logistic_regression')
    assert metadata['algorithm']=='Logistic Regression'
    assert metadata['feature_schema_version']==ABA_FEATURE_SCHEMA
    assert metadata['calibration_row_count']>0 and metadata['test_row_count']>0
    assert metadata['cohort_metrics'][0]['test_rows']>0
    assert metadata['metrics']['log_loss']>=0
    assert artifact['pipeline'] is artifact['explanation_pipeline']
    row=frame.iloc[0].to_dict();row['_feature_schema_version']=ABA_FEATURE_SCHEMA
    result=predict_with_artifact(artifact,'visa',row)
    assert 0<=result['visa_approval_probability']<=1
    assert result['explanation'] and 'contribution_log_odds' in result['explanation'][0]
    with pytest.raises(ValueError,match='feature schema'):
        predict_with_artifact(artifact,'visa',{**row,'_feature_schema_version':'visa-legacy-v1'})
    with pytest.raises(ValueError,match='cohort'):
        predict_with_artifact(artifact,'visa',{**row,'destination_country':'Australia'})


@pytest.mark.parametrize('column,value',[('aba_statement_strength',70),('aba_statement_quality',12),('evidence_strength_score',101)])
def test_training_preflight_rejects_wrong_feature_units(column,value):
    _,report=validate_training_dataset(pd.DataFrame([{'group_id':'s1','visa_outcome':'APPROVED',column:value}]),'visa')
    assert report['invalid_record_count']==1
    assert any('declared feature units' in reason for row in report['invalid_records'] for reason in row['issues'])


def test_percentage_compatibility_and_normalized_probability_are_explicit():
    old=reports._prediction_from_application({'predictionStatus':'COMPLETED','approvalProbability':78,'rejectionProbability':22})
    assert old['approvalProbability']==78
    assert old['approvalProbabilityNormalized']==.78
    assert old['probabilityUnit']=='percent' and old['normalizedProbabilityUnit']=='0-1'
    current=reports._prediction_from_application({'predictionStatus':'COMPLETED','approvalProbability':78.12,'rejectionProbability':21.88,'approvalProbabilityNormalized':.781234,'rejectionProbabilityNormalized':.218766})
    assert current['approvalProbabilityNormalized']==.781234


def test_dataset_template_exposes_new_features_without_sample_outcomes():
    rows=(Path(__file__).resolve().parents[2]/'templates'/'ml_training_dataset_template.csv').read_text(encoding='utf-8').splitlines()
    assert len(rows)==1
    assert {'visa_category','aba_statement_strength','aba_statement_quality','aba_average_daily_balance_usd','evidence_strength_score'}<=set(rows[0].split(','))


def test_aba_model_approval_requires_documented_release_criteria(entity_client):
    client,database=entity_client
    record={'_id':'aba-model-candidate','model_version':'aba-model-candidate','model_type':'visa','model_status':'UNDER_REVIEW','feature_schema_version':ABA_FEATURE_SCHEMA,'validation_passed':True,'validation_report':'Reviewed grouped heldout calibration metrics and cohort support.'}
    database['ml_model_versions'].documents.append(record)
    denied=client.post('/api/ml/models/aba-model-candidate/approve',headers=_headers('ADMIN'),json={'approval_comment':'Reviewed candidate for release'})
    assert denied.status_code==400
    assert 'releaseCriteria' in denied.json()['message']
    criteria='Accept only this reviewed Japan tourist cohort; assess calibration, outcome errors and known limitations in the attached review.'
    approved=client.post('/api/ml/models/aba-model-candidate/approve',headers=_headers('ADMIN'),json={'approval_comment':'Reviewed candidate for release','releaseCriteria':criteria})
    assert approved.status_code==200,approved.text
    assert record['releaseCriteria']==criteria
    assert record['model_status']=='APPROVED'


def test_receipt_refinalization_preserves_immutable_history_and_tokens(entity_client,monkeypatch,tmp_path):
    client,database=entity_client
    app,doc=_profile_case(database)
    monkeypatch.setattr(reports,'_RECEIPT_DIR',tmp_path)
    assert client.post('/api/verification/run',json={'applicationId':'case'},headers=_headers('STAFF')).status_code==200
    first=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','comments':'Reviewed initial synthetic evidence'},headers=_headers('STAFF'))
    assert first.status_code==200,first.text
    first_number=first.json()['receiptNumber'];first_token=app['receiptVerificationToken']
    first_pdf=(tmp_path/f'{first_number}.pdf').read_bytes()
    initial=client.get('/api/verification/results/case',headers=_headers('STAFF')).json()
    assert initial['receiptIsCurrent'] is True
    assert initial['receiptNumber']==first_number
    doc['revision']=2
    stale=client.get('/api/verification/results/case',headers=_headers('STAFF')).json()
    assert stale['receiptIsCurrent'] is False and stale['assessmentNeedsRefinalization'] is True
    assert stale['receiptNumber'] is None and stale['verificationReport'] is None
    assert stale['historicalReceipts'][-1]['receiptNumber']==first_number
    # Re-finalization cannot return an old result or bypass a current staff check.
    unreviewed=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','comments':'Reviewed replacement evidence'},headers=_headers('STAFF'))
    assert unreviewed.status_code==409
    assert client.post('/api/verification/run',json={'applicationId':'case'},headers=_headers('STAFF')).status_code==200
    second=client.post('/api/verification/case/finalize',json={'status':'VERIFIED','comments':'Reviewed revised synthetic evidence'},headers=_headers('STAFF'))
    assert second.status_code==200,second.text
    second_number=second.json()['receiptNumber']
    assert second_number!=first_number
    assert second.json()['report']['receiptVersion']==2
    assert second.json()['report']['supersedesReceiptNumber']==first_number
    assert (tmp_path/f'{first_number}.pdf').read_bytes()==first_pdf
    assert len(database['verificationreceipts'].documents)==2
    old=client.get(f'/api/verification/receipts/{first_number}',headers=_headers('STAFF'))
    assert old.status_code==200 and old.json()['historical'] is True
    assert old.json()['receipt']['receiptVersion']==1
    old_pdf=client.get(f'/api/verification/receipts/{first_number}/pdf',headers=_headers('STAFF'))
    assert old_pdf.status_code==200 and old_pdf.content==first_pdf
    old_qr=client.get(f'/api/verification/receipts/verify/{first_token}')
    assert old_qr.status_code==200 and old_qr.json()['valid'] is True
    assert old_qr.json()['historical'] is True and old_qr.json()['finalStatus']=='VERIFIED'
    fresh=client.get('/api/verification/results/case',headers=_headers('STAFF')).json()
    assert fresh['receiptIsCurrent'] is True and fresh['receiptNumber']==second_number
    app['customer']='somebody-else'
    assert client.get(f'/api/verification/receipts/{first_number}',headers=_headers('CUSTOMER')).status_code==404


def test_fingerprints_normalize_mongodb_utc_datetime_roundtrip():
    assert digest({'at':datetime(2026,1,1,tzinfo=timezone.utc)})==digest({'at':datetime(2026,1,1)})


def test_legacy_receipt_archive_matches_bson_roundtrip_without_rewriting(entity_client):
    _, database = entity_client
    at = datetime(2026, 11, 18, 2, 31, 20, 206789, tzinfo=timezone.utc)
    application = {
        '_id': 'synthetic-legacy-case', 'receiptNumber': 'SYNTHETIC-LEGACY-2',
        'receiptFilePath': '/synthetic/unchanged.pdf', 'receiptVerificationToken': 'synthetic-token',
        'receiptGeneratedAt': at,
        'verificationReport': {'receiptVersion': 2, 'receiptGeneratedAt': at, 'verifiedAt': at,
                               'summary': {'confirmedReadinessPoints': 85}, 'policyVersionId': 'old-policy'},
    }
    legacy = reports._receipt_archive_snapshot(application)
    legacy.pop('contentHashVersion')
    legacy['contentHash'] = digest(application['verificationReport'])
    stored = BSON.encode(legacy).decode()
    persisted_application = BSON.encode(application).decode()
    # Real BSON encoding loses microseconds and returns naive UTC datetimes.
    assert stored['contentHash'] != digest(stored['report'])
    database['verificationreceipts'].documents.append(stored)
    before = BSON.encode(stored)
    asyncio.run(reports._archive_receipt(database, persisted_application))
    assert BSON.encode(stored) == before
    assert len(database['verificationreceipts'].documents) == 1
    for field, value in [('receiptFilePath', '/synthetic/changed.pdf'),
                         ('receiptVerificationToken', 'changed-token'), ('_id', 'different-case')]:
        changed = {**persisted_application, field: value}
        with pytest.raises(APIError):
            asyncio.run(reports._archive_receipt(database, changed))
    changed = deepcopy(persisted_application)
    changed['verificationReport']['summary']['confirmedReadinessPoints'] = 80
    with pytest.raises(APIError):
        asyncio.run(reports._archive_receipt(database, changed))
    assert BSON.encode(stored) == before


def test_three_receipt_versions_survive_bson_storage_and_preserve_archives(entity_client, monkeypatch, tmp_path):
    client, database = entity_client
    app, document = _profile_case(database)
    monkeypatch.setattr(reports, '_RECEIPT_DIR', tmp_path)
    archives = {}
    for version in range(1, 4):
        document['revision'] = version
        verified = client.post('/api/verification/run', json={'applicationId': 'case'}, headers=_headers('STAFF'))
        assert verified.status_code == 200, verified.text
        final = client.post('/api/verification/case/finalize',
                            json={'status': 'VERIFIED', 'comments': f'Reviewed synthetic revision {version}'},
                            headers=_headers('STAFF'))
        assert final.status_code == 200, final.text
        assert final.json()['report']['receiptVersion'] == version
        number = final.json()['receiptNumber']
        # Exercise the actual serialization boundary omitted by the in-memory
        # entity fixture, including before the second re-finalization.
        persisted_app = BSON.encode(app).decode()
        app.clear()
        app.update(persisted_app)
        for record in database['verificationreceipts'].documents:
            persisted_record = BSON.encode(record).decode()
            record.clear()
            record.update(persisted_record)
            assert record['contentHashVersion'] == 'bson-ms-v1'
            assert record['contentHash'] == digest(reports._receipt_storage_value(record['report']))
            if record['_id'] in archives:
                assert BSON.encode(record) == archives[record['_id']][0]
                assert (tmp_path / f"{record['_id']}.pdf").read_bytes() == archives[record['_id']][1]
        current_record = next(record for record in database['verificationreceipts'].documents if record['_id'] == number)
        archives[number] = (BSON.encode(current_record), (tmp_path / f'{number}.pdf').read_bytes())
    assert len(archives) == 3
    assert len(app['receiptHistory']) == 2
    for number, (_, pdf) in archives.items():
        response = client.get(f'/api/verification/receipts/{number}/pdf', headers=_headers('STAFF'))
        assert response.status_code == 200 and response.content == pdf
    # Versioned hashes detect corruption of the immutable snapshot even if a
    # caller supplies the same corrupted report as its candidate.
    latest = database['verificationreceipts'].documents[-1]
    latest['report']['summary']['confirmedReadinessPoints'] = -1
    app['verificationReport'] = deepcopy(latest['report'])
    with pytest.raises(APIError):
        asyncio.run(reports._archive_receipt(database, app))
