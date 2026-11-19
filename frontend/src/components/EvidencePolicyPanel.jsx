import { useEffect, useMemo, useState } from 'react';
import { Check, FilePlus2, History, Save, ShieldCheck } from 'lucide-react';
import { api } from '../pages/pageUtils';
import './Integration.css';

export default function EvidencePolicyPanel({ visaTypes = [], requirements = [] }) {
  const [visaId, setVisaId] = useState('');
  const [profiles, setProfiles] = useState([]);
  const [selected, setSelected] = useState('');
  const [profile, setProfile] = useState(null);
  const [text, setText] = useState('[]');
  const [name, setName] = useState('');
  const [effective, setEffective] = useState('');
  const [reason, setReason] = useState('');
  const [version, setVersion] = useState('1');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dirty = Boolean(profile && (text !== JSON.stringify(profile.requirements || [], null, 2) || name !== (profile.name || '') || String(version) !== String(profile.version) || effective !== (profile.effectiveFrom?.slice(0,10) || '')));
  const sourceRequirements = useMemo(() => requirements.filter(r => String(r.visaTypeId) === visaId && r.status !== 'ARCHIVED'), [requirements, visaId]);
  const budget = useMemo(() => {
    try { const slots = JSON.parse(text); if (!Array.isArray(slots)) return null; const groups = new Map(); slots.forEach((r, i) => { const key = r.alternativeGroupId || r.requirementId || r._id || String(i); const value = Number(r.maximumEarnedPoints ?? r.weight ?? 0); groups.set(key, Math.max(groups.get(key) || 0, value)); }); return [...groups.values()].reduce((a, b) => a + b, 0); } catch { return null; }
  }, [text]);
  async function load(id = visaId) {
    if (!id) return;
    const { data } = await api.get('/policy-profiles', { params: { visaTypeId: id } }); setProfiles(data.profiles || []);
  }
  useEffect(() => { setProfiles([]); setSelected(''); setProfile(null); setName(''); setVersion('1'); setText('[]'); setError(''); if (visaId) load(visaId).catch(e => setError(e.response?.data?.message || e.message)); }, [visaId]);
  function useProfile(p) { setSelected(p._id || p.id); setProfile(p); setName(p.name || ''); setVersion(p.version || '1'); setEffective(p.effectiveFrom?.slice(0, 10) || ''); setText(JSON.stringify(p.requirements || [], null, 2)); setReason(''); setError(''); setNotice(''); }
  async function action(kind) {
    setBusy(true); setError(''); setNotice('');
    try {
      let response;
      if (kind === 'create') response = await api.post('/policy-profiles', { visaTypeId: visaId, name: name || 'Evidence policy', version, timezone: 'Asia/Phnom_Penh', ...(effective ? { effectiveFrom: effective } : {}) });
      else if (kind === 'save') response = await api.put(`/policy-profiles/${selected}`, { revision: profile.revision, name, version, requirements: JSON.parse(text), ...(effective ? { effectiveFrom: effective } : {}) });
      else response = await api.post(`/policy-profiles/${selected}/publish`, { revision: profile.revision, reason });
      const p = response.data.profile || response.data; useProfile(p); await load(); setNotice(kind === 'publish' ? 'Published immutable policy. Existing applications retain their original snapshot.' : 'Draft saved. Publish after all allocations and rubrics are valid.');
    } catch (e) { setError(e.response?.data?.message || e.message || 'Policy action failed.'); } finally { setBusy(false); }
  }
  return <section className="integration-panel policy-panel" aria-label="Published evidence scoring profiles">
    <div className="integration-heading"><div><h2>Evidence scoring profiles</h2><p>Publish a fixed 100-point budget for each country and visa type. Readiness is separate from approval probability.</p></div><ShieldCheck size={23} /></div>
    <div className="integration-form-grid">
      <label>Visa type<select value={visaId} onChange={e => setVisaId(e.target.value)}><option value="">Select a configured visa type</option>{visaTypes.map(v => <option key={v._id} value={v._id}>{v.visaTypeCode || v.name} · {v.destination}</option>)}</select></label>
      <label>Saved versions<select disabled={!visaId} value={selected} onChange={e => { const p = profiles.find(p => (p._id || p.id) === e.target.value); if (p) useProfile(p); }}><option value="">Choose a policy version</option>{profiles.map(p => <option key={p._id || p.id} value={p._id || p.id}>{p.name} · {p.version} · {p.status}</option>)}</select></label>
      <label>Policy name<input value={name} onChange={e => setName(e.target.value)} disabled={profile?.status === 'PUBLISHED'} placeholder="Internal visitor evidence policy" /></label>
      <label>Version<input value={version} disabled={profile?.status === 'PUBLISHED'} onChange={e => setVersion(e.target.value)} placeholder="1.0" /></label>
      <label>Effective from<input type="date" value={effective} disabled={profile?.status === 'PUBLISHED'} onChange={e => setEffective(e.target.value)} /></label>
    </div>
    {error && <p role="alert" className="integration-error">{error}</p>}{notice && <p role="status" className="integration-success">{notice}</p>}
    <div className="integration-actions"><button type="button" disabled={!visaId || busy || !sourceRequirements.length} onClick={() => action('create')}><FilePlus2 size={16} /> Create draft from current requirements</button><span>{sourceRequirements.length} active requirement definitions</span></div>
    {profile && <>
      <div className="integration-budget"><strong>Allocated: {budget === null ? 'Invalid JSON' : `${budget.toFixed(2)} / 100`}</strong><span>{profile.status} · version {profile.version} · revision {profile.revision}</span></div>
      <p className="integration-note">Fixed slots award their allocation after verification. Dynamic slots need a versioned rubric or the ABA scorer. Alternatives share one slot budget. Saved drafts do not change published applications.</p>
      <details><summary>Requirement slots, scoring ranges and applicability</summary><label className="integration-code-label">Profile requirement JSON<textarea rows={18} value={text} disabled={profile.status === 'PUBLISHED'} spellCheck={false} onChange={e => setText(e.target.value)} /></label><p className="integration-note">Amounts are points, not money. For a manual range use qualityScorer MANUAL_RUBRIC and a versioned criteria/levels qualityRubric. Publication validates the full budget and rejects unsupported rules.</p></details>
      {profile.status === 'DRAFT' ? <><div className="integration-actions"><button type="button" disabled={busy || budget === null} onClick={() => action('save')}><Save size={16} /> Save draft</button></div><label>Publication reason<input value={reason} minLength={5} onChange={e => setReason(e.target.value)} placeholder="Why this version is suitable for this visa profile" /></label><div className="integration-actions"><button type="button" disabled={busy || dirty || reason.trim().length < 5 || Math.abs((budget ?? 0) - 100) > 0.00001} onClick={() => action('publish')}><Check size={16} /> Publish saved draft</button><span>Save edits before publishing. The server validates the saved revision. Unsaved changes disable publication.</span></div></> : <p className="integration-success"><History size={16} /> Published versions are immutable. Create a new draft to change policy.</p>}
    </>}
    {!visaTypes.length && <p className="integration-note">Create a country, visa type and requirements below, then publish its evidence profile here.</p>}
  </section>;
}
