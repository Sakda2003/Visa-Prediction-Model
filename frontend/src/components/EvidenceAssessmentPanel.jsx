import { useEffect, useState } from 'react';
import { Check, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../pages/pageUtils';
import './Integration.css';

function points(value) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return 'Unavailable';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value));
}

function recordedTime(value) {
  if (!value) return 'Time unavailable';
  // MongoDB's naive datetimes represent UTC; never reinterpret them as browser-local time.
  const timestamp = String(value);
  const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`);
  if (Number.isNaN(date.getTime())) return 'Time unavailable';
  return date.toLocaleString('en-GB', { timeZone: 'Asia/Phnom_Penh', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function AssessmentHistory({ entries = [] }) {
  const ordered = [...entries].filter(entry => entry && typeof entry === 'object').sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || '')));
  if (!ordered.length) return <details className="mt-5"><summary>Assessment history (0)</summary><p className="integration-note">No reassessments or rubric reviews have been recorded for this application.</p></details>;
  const slotKey = slot => String(slot.slotId || slot.requirementId || slot.name || '');
  const total = assessment => assessment?.confirmedReadinessPoints ?? assessment?.documentAssessmentScore ?? assessment?.earnedPoints;
  return <details className="mt-5"><summary>Assessment history ({ordered.length})</summary>
    <p className="integration-note">Read-only comparisons from recorded assessments. Times use Asia/Phnom_Penh. Reconstructed figures are labeled and do not establish what the historical evidence contained.</p>
    {ordered.map((entry, index) => {
      const previous = entry.previousAssessment;
      const next = entry.assessment;
      const origin = entry.previousAssessmentOrigin || 'UNAVAILABLE';
      const previousSlots = new Map((previous?.requirementScores || []).map(slot => [slotKey(slot), slot]));
      const nextSlots = new Map((next?.requirementScores || []).map(slot => [slotKey(slot), slot]));
      const keys = [...new Set([...previousSlots.keys(), ...nextSlots.keys()])];
      const previousLabel = origin === 'RECONSTRUCTED_CURRENT_EVIDENCE' ? 'Previous policy with current evidence (reconstructed)' : origin === 'PERSISTED_ASSESSMENT' ? 'Saved previous assessment' : 'Previous assessment unavailable';
      return <details className="mt-4 border-t border-slate-200 pt-3" key={`${entry.createdAt || 'undated'}-${entry.policyVersionId || 'review'}-${index}`}>
        <summary>{recordedTime(entry.createdAt)} · {entry.previousPolicyVersionId ? 'Policy reassessment' : 'Evidence review'} · {points(total(next))}{total(next) == null ? '' : ' points'}</summary>
        <p className="integration-note"><strong>Reason:</strong> {entry.reason || 'No reason recorded'}<br/><strong>Reviewer:</strong> {entry.reviewedBy || 'Not recorded'}</p>
        {origin === 'RECONSTRUCTED_CURRENT_EVIDENCE' && <p className="integration-warning"><strong>Reconstructed comparison — not a historical result.</strong> {entry.previousAssessmentNote || 'No saved previous assessment existed. These figures use the previous policy and current evidence.'}</p>}
        {origin === 'UNAVAILABLE' && <p className="integration-note">{entry.previousAssessmentNote || 'No saved prior assessment is available. Previous points are unknown, not zero.'}</p>}
        <div className="integration-table-wrap" role="region" aria-label={`Assessment comparison ${index + 1}`} tabIndex={0}>
          <table className="integration-table"><thead><tr><th>Comparison</th><th>{previousLabel}</th><th>Recorded new assessment</th></tr></thead>
            <tbody><tr><td>Policy version</td><td className="integration-description">{entry.previousPolicySnapshot?.version || entry.previousPolicyVersionId || 'Unavailable'}</td><td className="integration-description">{next?.policyVersion || entry.policyVersionId || 'Unavailable'}</td></tr>
              <tr><td>Policy reference</td><td className="integration-description">{entry.previousPolicyVersionId || entry.previousPolicySnapshot?.id || 'Unavailable'}</td><td className="integration-description">{entry.policyVersionId || 'Unavailable'}</td></tr>
              <tr><td>Confirmed evidence points</td><td>{points(total(previous))}</td><td>{points(total(next))}</td></tr>
              {keys.map(key => {
                const before = previousSlots.get(key);
                const after = nextSlots.get(key);
                return <tr key={key}><td className="integration-description">{after?.name || before?.name || key}</td><td>{before ? `${points(before.earnedPoints)} / ${points(before.configuredPoints ?? before.maximumEarnedPoints)}` : previous ? 'Not in previous profile' : 'Unavailable'}</td><td>{after ? `${points(after.earnedPoints)} / ${points(after.configuredPoints ?? after.maximumEarnedPoints)}` : next ? 'Not in new profile' : 'Unavailable'}</td></tr>;
              })}
            </tbody></table>
        </div>
      </details>;
    })}
  </details>;
}

function QualityReview({ slot, requirement, documents, applicationId, onChange }) {
  const [documentId,setDocumentId]=useState(slot.selectedDocumentId||slot.documentIds?.[0]||'');
  const [scores,setScores]=useState({});const [level,setLevel]=useState('');const [reason,setReason]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const rubric=requirement?.qualityRubric||{};
  const options=documents.filter(d=>slot.documentIds?.includes(d.documentId));
  async function save(e){e.preventDefault();setBusy(true);setError('');try{await api.post(`/policy-profiles/applications/${applicationId}/review-requirement`,{slotId:slot.slotId,documentId,reason,...(rubric.levels?{levelId:level}:{criterionScores:scores})});setReason('');await onChange();}catch(err){setError(err.response?.data?.message||err.message);}finally{setBusy(false);}}
  return <form onSubmit={save} className="integration-quality-form"><h3 className="font-semibold mb-3">Review: {slot.name}</h3><p className="integration-note">A quality review records evidence strength. The document must separately be verified before these points become confirmed.</p><div className="integration-form-grid"><label>Evidence document<select required value={documentId} onChange={e=>{setDocumentId(e.target.value);setReason('');setScores({});setLevel('');}}><option value="">Choose an uploaded document</option>{options.map(d=><option key={d.documentId} value={d.documentId}>{d.fileName} · {d.status}</option>)}</select></label>{rubric.levels?<label>Rubric level<select required value={level} onChange={e=>setLevel(e.target.value)}><option value="">Choose the evidenced level</option>{rubric.levels.map(l=><option key={l.id} value={l.id}>{l.label||l.id} · {(Number(l.quality)*100).toFixed(0)}% quality</option>)}</select></label>:rubric.criteria?.map(c=><label key={c.id}>{c.label||c.id} (0–{c.maximumPoints})<input required type="number" min="0" max={c.maximumPoints} step="0.01" value={scores[c.id]??''} onChange={e=>setScores(s=>({...s,[c.id]:e.target.value}))}/></label>)}</div><label>Reason and source references<textarea required minLength={8} rows={2} value={reason} onChange={e=>setReason(e.target.value)}/></label>{error&&<p role="alert" className="integration-error">{error}</p>}<div className="integration-actions"><button disabled={busy||!documentId||reason.trim().length<8} type="submit"><Check size={16}/>Record rubric review</button></div></form>;
}

export default function EvidenceAssessmentPanel({ results, applicationId, canManage, onChange }) {
  const summary=results.summary||{};const application=results.application||{};const prediction=results.prediction||{};
  const slots=summary.requirementScores||[];const canonical=summary.scoringVersion==='evidence-v3';
  const [profiles,setProfiles]=useState([]);const [chosen,setChosen]=useState('');const [reason,setReason]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [reviewSlot,setReviewSlot]=useState('');
  useEffect(()=>{if(!canManage||!application.visaTypeId)return;let active=true;api.get('/policy-profiles',{params:{visaTypeId:application.visaTypeId}}).then(r=>{if(active)setProfiles((r.data.profiles||[]).filter(p=>p.status==='PUBLISHED'));}).catch(e=>{if(active)setError(e.response?.data?.message||'Could not load published policy versions.');});return()=>{active=false;};},[canManage,application.visaTypeId]);
  async function download(kind){setError('');try{const r=await api.get(`/assessments/${applicationId}/exports/${kind}`,{responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`evidence-assessment.${kind}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError('Could not export this authorized assessment. Refresh and try again.');}}
  async function verify(){setBusy(true);setError('');try{await api.post('/verification/run',{applicationId});await onChange();}catch(e){setError(e.response?.data?.message||e.message);}finally{setBusy(false);}}
  async function reassess(e){e.preventDefault();setBusy(true);setError('');try{await api.post(`/policy-profiles/applications/${applicationId}/reassess`,{policyVersionId:chosen,reason});setReason('');await onChange();}catch(err){setError(err.response?.data?.message||err.message);}finally{setBusy(false);}}
  const probability=prediction.approvalProbabilityNormalized??(prediction.approvalProbability==null?null:Number(prediction.approvalProbability)/100);
  return <section className="integration-panel" aria-label="Canonical evidence assessment">
    <div className="integration-heading"><div><h2>Evidence strength and prediction</h2></div><ShieldCheck size={23}/></div>
    {error&&<p role="alert" className="integration-error">{error}</p>}
    <div className="integration-disclosure-grid">
      <div><small>Confirmed evidence strength</small><strong className="integration-number">{canonical?summary.confirmedReadinessPoints??'—':summary.documentAssessmentScore??'—'} <small>/ 100</small></strong>
        <p className="integration-note">{canonical ? String(summary.status||'Review required').toLowerCase().replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase()) : 'Legacy assessment'}</p>
        {summary.completionRange && Number(summary.completionRange.upper)>Number(summary.completionRange.lower) && <p className="integration-note">Potential after review: {summary.completionRange.lower}–{summary.completionRange.upper} points.</p>}
      </div>
      <div className="integration-probability"><small>Estimated visa approval probability</small><strong className="integration-number">{probability==null||prediction.status!=='COMPLETED'?'Unavailable':`${(probability*100).toFixed(1)}%`}</strong>
        <p className="integration-note">{prediction.status==='STALE'?'Evidence changed. Refresh the assessment.':probability==null?'A validated model and verified inputs are required.':`Model ${prediction.modelVersion||'—'}`}</p>
      </div>
    </div>
    <p className="integration-note">Evidence points are not an approval probability.</p>
    <details className="mt-4"><summary>Assessment details</summary><dl className="integration-note"><dt>Policy</dt><dd>{summary.policyVersion||'legacy'} · {summary.policyVersionId||application.policyVersionId||'No published profile selected'}</dd><dt>Normalized evidence score</dt><dd>{summary.confirmedReadinessNormalized==null?'Unavailable':Number(summary.confirmedReadinessNormalized).toFixed(4)}</dd></dl></details>
    {(summary.gates||[]).map((g,i)=><div className="integration-warning" key={g.slotId||i}><strong>{g.name||'Assessment gate'}</strong><ul className="list-disc pl-5">{(g.reasons||[g.reason||g]).map((r,j)=><li key={j}>{String(r)}</li>)}</ul></div>)}
    <div className="integration-table-wrap" role="region" aria-label="Evidence points ledger" tabIndex={0}><table className="integration-table"><thead><tr><th>Requirement slot</th><th>Mode</th><th>Quality</th><th>Confirmed points</th><th>Maximum</th><th>State / reason</th></tr></thead><tbody>{slots.map((s,i)=><tr key={s.slotId||s.requirementId||i}><td className="integration-description">{s.name}<small className="block text-slate-500">{s.required?'Required for final assessment':'Optional / supplemental'}</small></td><td>{s.scoringMode||'Legacy'}</td><td>{s.quality==null?'Unknown':`${(Number(s.quality)*100).toFixed(2)}%`}</td><td>{s.earnedPoints??0}</td><td>{s.configuredPoints??s.maximumEarnedPoints??0}</td><td className="integration-description">{s.status}{s.reasons?.length>0&&<p>{s.reasons.join('; ')}</p>}{canManage&&canonical&&results.requirements?.some(r=>r.requirementId===s.requirementId&&r.qualityScorer==='MANUAL_RUBRIC')&&<button type="button" onClick={()=>setReviewSlot(reviewSlot===s.slotId?'':s.slotId)}>Review quality rubric</button>}</td></tr>)}</tbody></table></div>
    {canManage&&canonical&&reviewSlot&&slots.filter(s=>s.slotId===reviewSlot).map(s=><QualityReview key={`${s.slotId}-${s.selectedDocumentId}`} slot={s} requirement={results.requirements?.find(r=>r.requirementId===s.requirementId)} documents={results.documents||[]} applicationId={applicationId} onChange={onChange}/>)}
    <div className="integration-actions">{['json','csv','html'].map(kind=><button type="button" key={kind} onClick={()=>download(kind)}>Export {kind.toUpperCase()}</button>)}</div>
    <AssessmentHistory entries={results.assessmentHistory || []} />
    {canManage&&<><div className="integration-actions"><button type="button" disabled={busy} onClick={verify}><RefreshCw size={16}/>Refresh assessment and verification</button></div><details className="mt-5"><summary>Explicitly reassess against another published policy</summary><p className="integration-note">This creates a new assessment history entry and invalidates dependent predictions. It does not change the original recorded result.</p><form onSubmit={reassess} className="integration-review-form"><label>Published policy<select required value={chosen} onChange={e=>setChosen(e.target.value)}><option value="">Choose the reviewed version</option>{profiles.map(p=><option key={p._id} value={p._id}>{p.name} · {p.version}</option>)}</select></label><label>Reassessment reason<input required minLength={5} value={reason} onChange={e=>setReason(e.target.value)}/></label><div className="integration-actions"><button disabled={busy||!chosen||reason.trim().length<5} type="submit">Reassess with selected policy</button></div></form></details></>}
  </section>;
}
