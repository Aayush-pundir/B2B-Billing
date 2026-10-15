// ---------------------------------------------------------------------------
// Billing engine v2 workspace: accounts, versioned contracts, lifecycle, simulator,
// exceptions, groups, usage inputs and imports. Finance amends; others read.
// ---------------------------------------------------------------------------

const v2Money = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sevBadge = { high: 'error', medium: 'draft', low: 'none', info: 'none' };
let V2CAT = null;
const v2cat = async () => V2CAT || (V2CAT = await Apx.v2('/catalogue'));

async function renderAccountsV2(content) {
  const tab = state.v2Tab || 'accounts';
  if (state.v2Account && tab === 'accounts') return renderAccountDetail(content, state.v2Account);
  const tabs = [['accounts', 'Accounts', 'fa-building-user'], ['exceptions', 'Exceptions', 'fa-triangle-exclamation'], ['groups', 'Groups', 'fa-people-group'], ['usage', 'Usage inputs', 'fa-database'], ['import', 'Import & mapping', 'fa-file-import']];
  content.innerHTML = `${isFinance() ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-lock"></i><span>Only Finance can amend terms. You can view everything here.</span></div>'}${tabBar(tabs, tab)}<div id="v2-panel"></div>`;
  bindTabs(content, 'v2Tab', 'accounts');
  content.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => { state.v2Account = null; }));
  const panel = document.getElementById('v2-panel');
  ({ accounts: v2AccountsTab, exceptions: v2ExceptionsTab, groups: v2GroupsTab, usage: v2UsageTab, import: v2ImportTab })[tab](panel);
}

// ------------------------------- accounts list -------------------------------
async function v2AccountsTab(panel) {
  const q = state.v2Q || '', st = state.v2Filter || '';
  const rows = await Apx.v2(`/accounts?q=${encodeURIComponent(q)}&status=${st}`);
  const unmapped = rows.filter(r => !r.mapped).length, unmodelled = rows.reduce((s, r) => s + r.unmodelled, 0);
  panel.innerHTML = `
    <div class="stat-grid"><div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-building-user"></i></div><div class="value">${rows.length}</div><div class="label">Billing accounts</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-link-slash"></i></div><div class="value">${unmapped}</div><div class="label">Awaiting enterprise-ID mapping</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-puzzle-piece"></i></div><div class="value">${unmodelled}</div><div class="label">Charges still to model</div></div></div>
    <div class="card"><div class="actions-row"><input id="v2-q" placeholder="Search name or enterprise ID…" value="${esc(q)}" style="min-width:260px" />
      <select id="v2-f"><option value="">All</option><option value="billable" ${st === 'billable' ? 'selected' : ''}>Billable</option><option value="nonbillable" ${st === 'nonbillable' ? 'selected' : ''}>Not billable</option></select></div>
      ${tbl(['Account', 'Enterprise IDs', 'State', 'Contract', 'Charges', '>Minimum', 'Open issues', ''], rows.map(r => `<tr data-id="${r.id}">
        <td><b>${esc(r.name)}</b><div class="muted" style="font-size:.74rem">${esc(r.kam || '')}</div></td>
        <td class="mono" style="white-space:normal;max-width:200px">${r.mapped ? esc(r.enterprise_ids.join(', ')) : '<span class="badge draft">not mapped</span>'}</td>
        <td><span class="badge ${r.billable ? 'locked' : 'none'}">${esc(r.state)}</span></td>
        <td>${r.contract_version ? `v${r.contract_version} <span class="badge ${r.contract_status === 'locked' ? 'locked' : 'draft'}">${esc(r.contract_status)}</span>` : `<span class="badge ${r.contract_status === 'draft' ? 'draft' : 'none'}">${esc(r.contract_status)}</span>`}</td>
        <td>${r.components}${r.unmodelled ? ` <span class="badge error">${r.unmodelled} to model</span>` : ''}</td>
        <td class="right amount">${r.overall_min ? v2Money(r.overall_min) : '—'}</td>
        <td>${r.open_exceptions ? `<span class="badge draft">${r.open_exceptions}</span>` : '—'}</td>
        <td><button class="ghost v2-open">Open</button></td></tr>`), 'No billing accounts yet — import your rulebook on the Import tab.')}</div>`;
  let t; panel.querySelector('#v2-q').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { state.v2Q = e.target.value; v2AccountsTab(panel); }, 300); });
  panel.querySelector('#v2-f').addEventListener('change', e => { state.v2Filter = e.target.value; v2AccountsTab(panel); });
  panel.querySelectorAll('.v2-open').forEach(b => b.addEventListener('click', e => { state.v2Account = Number(e.target.closest('tr').dataset.id); state.v2Sub = 'contract'; navigate('accounts'); }));
}

// ------------------------------- account detail -------------------------------
async function renderAccountDetail(content, id) {
  const [a, cat] = await Promise.all([Apx.v2(`/accounts/${id}`), v2cat()]);
  const sub = state.v2Sub || 'contract';
  const tabs = [['contract', 'Contract', 'fa-file-contract'], ['enterprises', 'Enterprises', 'fa-link'], ['lifecycle', 'Lifecycle', 'fa-timeline'], ['subs', 'Subscriptions', 'fa-calendar-check'], ['esc', 'Escalation', 'fa-arrow-trend-up'], ['sim', 'Simulate', 'fa-flask']];
  content.innerHTML = `
    <div class="actions-row"><button class="ghost" id="v2-back"><i class="fa-solid fa-arrow-left"></i> All accounts</button></div>
    <div class="card"><div class="flex-between"><div><h3 style="margin:0">${esc(a.name)}</h3><div class="muted">${esc(a.zoho_billing_name || '')}${a.kam ? ' · KAM ' + esc(a.kam) : ''}${a.go_live_date ? ' · live ' + esc(a.go_live_date) : ''}</div></div>
      <div>${a.groups.map(g => `<span class="badge draft">${esc(g.group)}${g.is_parent ? ' (parent)' : ''}</span>`).join(' ')} ${a.auto_debit ? '' : '<span class="badge draft">no auto-debit</span>'}</div></div>
      ${a.import_flags.length ? `<details class="fallback"><summary>${a.import_flags.length} note(s) carried over from the legacy sheet</summary>${a.import_flags.map(f => `<div class="muted" style="font-size:.8rem;margin-top:.3rem">• ${esc(f)}</div>`).join('')}</details>` : ''}</div>
    ${tabBar(tabs, sub)}<div id="v2-sub"></div>`;
  document.getElementById('v2-back').onclick = () => { state.v2Account = null; navigate('accounts'); };
  content.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => { state.v2Sub = b.dataset.tab; renderAccountDetail(content, id); }));
  const el = document.getElementById('v2-sub'), reload = () => renderAccountDetail(content, id);
  const call = async (fn, msg) => { try { await fn(); if (msg) toast(msg); reload(); } catch (e) { toast(e.message, true); } };
  ({ contract: () => v2Contract(el, a, cat, reload, call), enterprises: () => v2Enterprises(el, a, call), lifecycle: () => v2Lifecycle(el, a, cat, call),
     subs: () => v2Subs(el, a, call), esc: () => v2Esc(el, a, call), sim: () => v2Sim(el, a) })[sub]();
}

function paramField(def, val) {
  const v = val ?? '';
  if (def.kind === 'json' || def.kind === 'list') return `<label>${esc(def.label)}<textarea data-p="${def.key}" data-kind="${def.kind}" rows="3" class="mono">${esc(typeof v === 'string' ? v : JSON.stringify(v, null, 0))}</textarea></label>`;
  return `<label>${esc(def.label)}<input data-p="${def.key}" data-kind="${def.kind}" ${def.kind === 'number' ? 'type="number" step="any"' : ''} value="${esc(v)}" /></label>`;
}

function v2Contract(el, a, cat, reload, call) {
  const cs = a.contracts;
  if (!cs.length) { el.innerHTML = `<div class="card"><p class="muted">No contract yet.</p>${isFinance() ? '<button id="c-new">Create first version</button>' : ''}</div>`; document.getElementById('c-new')?.addEventListener('click', () => call(() => Apx.v2(`/accounts/${a.id}/contracts?copy_from_latest=false`, { method: 'POST' }), 'Draft created')); return; }
  const cur = cs.find(c => c.id === state.v2Contract) || cs.find(c => c.status === 'locked') || cs[0];
  const edit = cur.status === 'draft' && isFinance();
  const compRow = (k) => {
    const defs = cat.components[k.component_type]?.params || [];
    return `<div class="comp card" data-type="${esc(k.component_type)}"><div class="flex-between"><div>${edit ? `<select class="c-type">${Object.entries(cat.components).map(([t, d]) => `<option value="${t}" ${t === k.component_type ? 'selected' : ''}>${esc(d.label)}</option>`).join('')}</select>` : `<b>${esc(cat.components[k.component_type]?.label || k.component_type)}</b>`}
      <span class="badge none">${esc(cat.components[k.component_type]?.group || '')}</span> ${k.component_type === 'manual_formula' ? '<span class="badge error">needs modelling</span>' : ''}</div>${edit ? '<i class="fa-solid fa-trash c-rm" style="cursor:pointer;color:var(--danger)"></i>' : ''}</div>
      <div class="form-grid">${edit ? `<label>Label<input class="c-label" value="${esc(k.label)}" /></label>` : ''}${defs.map(d => paramField(d, k.params?.[d.key])).join('')}</div>
      ${edit ? '' : `<div class="muted mono" style="font-size:.74rem">${esc(JSON.stringify(k.params))}</div>`}</div>`;
  };
  el.innerHTML = `
    <div class="card"><div class="flex-between"><h3 style="margin:0">Contract versions</h3>
      <div class="actions-row" style="margin:0"><select id="c-ver">${cs.map(c => `<option value="${c.id}" ${c.id === cur.id ? 'selected' : ''}>v${c.version} · ${c.status} · from ${esc(c.valid_from)}${c.valid_to ? ' to ' + esc(c.valid_to) : ''}</option>`).join('')}</select>
        ${isFinance() ? '<button class="ghost" id="c-new"><i class="fa-solid fa-code-branch"></i> New version</button>' : ''}</div></div>
      <p class="muted">${cur.status === 'draft' ? 'Draft — editable. Lock it to make it the billing basis.' : `${esc(cur.status)} — read-only history. ${cur.approved_by ? 'Approved by ' + esc(cur.approved_by) + '.' : ''}`} ${cur.notes ? '· ' + esc(cur.notes) : ''}</p>
      <div class="form-grid">
        <label>Valid from<input type="date" id="c-from" value="${esc(cur.valid_from)}" ${edit ? '' : 'disabled'} /></label>
        <label>Valid to<input type="date" id="c-to" value="${esc(cur.valid_to || '')}" ${edit ? '' : 'disabled'} /></label>
        <label>Overall minimum ₹<input type="number" step="any" id="c-min" value="${cur.overall_min_amount ?? ''}" ${edit ? '' : 'disabled'} /></label>
        <label>Minimum applies from<input type="date" id="c-mgfrom" value="${esc(cur.mg_effective_from || '')}" ${edit ? '' : 'disabled'} /></label>
        <label>Monthly cap ₹<input type="number" step="any" id="c-cap" value="${cur.overall_cap_amount ?? ''}" ${edit ? '' : 'disabled'} /></label>
        <label>Pro-rate the minimum<select id="c-prm" ${edit ? '' : 'disabled'}><option value="false" ${cur.prorate_mg ? '' : 'selected'}>no</option><option value="true" ${cur.prorate_mg ? 'selected' : ''}>yes</option></select></label>
        <label>Subscription covers<input id="c-cov" value="${esc((cur.subscription_covers || ['A']).join(','))}" ${edit ? '' : 'disabled'} title="Component groups: A platform, B UPI, C users, D module" /></label>
        <label>GST rate<input type="number" step="any" id="c-gst" value="${cur.gst_rate}" ${edit ? '' : 'disabled'} /></label></div></div>
    <div id="c-comps">${cur.components.map(compRow).join('') || '<div class="card muted">No charges in this version.</div>'}</div>
    ${edit ? `<div class="actions-row"><button class="ghost" id="c-add"><i class="fa-solid fa-plus"></i> Add charge</button><button id="c-save"><i class="fa-solid fa-floppy-disk"></i> Save draft</button><button class="secondary" id="c-lock"><i class="fa-solid fa-lock"></i> Lock version</button></div>` : ''}`;
  document.getElementById('c-ver').onchange = e => { state.v2Contract = Number(e.target.value); reload(); };
  document.getElementById('c-new')?.addEventListener('click', () => call(async () => { const r = await Apx.v2(`/accounts/${a.id}/contracts`, { method: 'POST' }); state.v2Contract = r.id; }, 'New draft version created'));
  if (!edit) return;
  const wireRow = (card) => { card.querySelector('.c-rm').onclick = () => card.remove(); card.querySelector('.c-type').onchange = (e) => { const k = { component_type: e.target.value, label: '', params: {} }; card.outerHTML = compRow(k); wireAll(); }; };
  const wireAll = () => el.querySelectorAll('.comp').forEach(wireRow);
  wireAll();
  document.getElementById('c-add').onclick = () => { document.getElementById('c-comps').insertAdjacentHTML('beforeend', compRow({ component_type: 'fixed_saas', label: '', params: {} })); wireAll(); };
  const collect = () => ({
    valid_from: document.getElementById('c-from').value, valid_to: document.getElementById('c-to').value || null,
    overall_min_amount: document.getElementById('c-min').value === '' ? null : Number(document.getElementById('c-min').value),
    overall_cap_amount: document.getElementById('c-cap').value === '' ? null : Number(document.getElementById('c-cap').value),
    mg_effective_from: document.getElementById('c-mgfrom').value || null, prorate_mg: document.getElementById('c-prm').value === 'true',
    subscription_covers: document.getElementById('c-cov').value.split(',').map(x => x.trim()).filter(Boolean), gst_rate: Number(document.getElementById('c-gst').value || 0.18), notes: cur.notes, billing_frequency: cur.billing_frequency, gst_mode: cur.gst_mode,
    components: [...el.querySelectorAll('.comp')].map(card => {
      const params = {};
      card.querySelectorAll('[data-p]').forEach(i => { const raw = i.value.trim(); if (raw === '') return; try { params[i.dataset.p] = i.dataset.kind === 'number' ? Number(raw) : (i.dataset.kind === 'json' || i.dataset.kind === 'list') ? JSON.parse(raw) : raw; } catch (e) { throw new Error(`"${i.dataset.p}" is not valid JSON`); } });
      return { component_type: card.querySelector('.c-type').value, label: card.querySelector('.c-label').value, params };
    }),
  });
  document.getElementById('c-save').onclick = () => call(() => Apx.v2(`/contracts/${cur.id}`, { method: 'PUT', body: collect() }), 'Draft saved');
  document.getElementById('c-lock').onclick = () => call(async () => { await Apx.v2(`/contracts/${cur.id}`, { method: 'PUT', body: collect() }); await Apx.v2(`/contracts/${cur.id}/lock`, { method: 'POST' }); }, 'Version locked — it is now the billing basis');
}

function v2Enterprises(el, a, call) {
  const row = (e) => `<tr><td><input class="e-id" value="${esc(e.enterprise_id)}" /></td><td><select class="e-role"><option ${e.role === 'primary' ? 'selected' : ''}>primary</option><option ${e.role === 'secondary' ? 'selected' : ''}>secondary</option></select></td>
    ${['include_add_money', 'include_upi', 'include_users'].map(k => `<td><input type="checkbox" class="e-${k}" ${e[k] ? 'checked' : ''} /></td>`).join('')}<td><input type="date" class="e-from" value="${esc(e.valid_from || '')}" /></td><td><input type="date" class="e-to" value="${esc(e.valid_to || '')}" /></td><td><i class="fa-solid fa-trash e-rm" style="cursor:pointer;color:var(--danger)"></i></td></tr>`;
  el.innerHTML = `<div class="card"><h3>Platform enterprises billed under this account</h3><p class="muted">Usage is summed across these enterprises (clubbed accounts). Everything is keyed by enterprise ID — never by name.</p>
    <div class="table-wrap"><table><thead><tr><th>Enterprise ID</th><th>Role</th><th>Add Money</th><th>UPI</th><th>Users</th><th>From</th><th>To</th><th></th></tr></thead><tbody id="e-rows">${a.enterprises.map(row).join('')}</tbody></table></div>
    ${isFinance() ? '<div class="actions-row"><button class="ghost" id="e-add"><i class="fa-solid fa-plus"></i> Add enterprise</button><button id="e-save">Save</button></div>' : ''}</div>`;
  if (!isFinance()) return;
  const wire = () => el.querySelectorAll('.e-rm').forEach(b => b.onclick = e => e.target.closest('tr').remove());
  wire();
  document.getElementById('e-add').onclick = () => { document.getElementById('e-rows').insertAdjacentHTML('beforeend', row({ enterprise_id: '', role: 'secondary', include_add_money: true, include_upi: true, include_users: true })); wire(); };
  document.getElementById('e-save').onclick = () => call(() => Apx.v2(`/accounts/${a.id}/enterprises`, { method: 'PUT', body: [...el.querySelectorAll('#e-rows tr')].map(r => ({ enterprise_id: r.querySelector('.e-id').value, role: r.querySelector('.e-role').value, include_add_money: r.querySelector('.e-include_add_money').checked, include_upi: r.querySelector('.e-include_upi').checked, include_users: r.querySelector('.e-include_users').checked, valid_from: r.querySelector('.e-from').value || null, valid_to: r.querySelector('.e-to').value || null })) }), 'Enterprises saved');
}

function v2Lifecycle(el, a, cat, call) {
  el.innerHTML = `<div class="card"><h3>Lifecycle events</h3><p class="muted">Dated status changes replace the free-text remarks. Billing reads these — e.g. a drop with an effective date bills the days before it.</p>
    ${tbl(['Event', 'From', 'To', 'Detail', ''], a.events.map(e => `<tr data-id="${e.id}"><td><b>${esc(e.event_type.replace(/_/g, ' '))}</b></td><td>${esc(e.effective_from || '—')}</td><td>${esc(e.effective_to || '—')}</td><td class="muted" style="white-space:normal">${esc(e.source_text || '')} ${Object.keys(e.payload || {}).length ? `<span class="mono">${esc(JSON.stringify(e.payload))}</span>` : ''}</td><td>${isFinance() ? '<i class="fa-solid fa-trash ev-rm" style="cursor:pointer;color:var(--danger)"></i>' : ''}</td></tr>`), 'No events — the account is simply active.')}
    ${isFinance() ? `<h4>Add event</h4><div class="form-grid"><label>Type<select id="ev-type">${cat.events.map(t => `<option>${t}</option>`).join('')}</select></label><label>Effective from<input type="date" id="ev-from" /></label><label>Effective to<input type="date" id="ev-to" /></label>
      <label>Note<input id="ev-note" /></label><label>Payload (JSON, optional)<input id="ev-payload" placeholder='{"policy":"bill"} for not_using, {"mg_effective_from":"2026-10-01"}' /></label></div><div class="actions-row" style="margin-top:.8rem"><button id="ev-add">Add event</button></div>` : ''}</div>`;
  el.querySelectorAll('.ev-rm').forEach(b => b.onclick = e => call(() => Apx.v2(`/events/${e.target.closest('tr').dataset.id}`, { method: 'DELETE' }), 'Event removed'));
  document.getElementById('ev-add')?.addEventListener('click', () => { let payload = {}; try { payload = JSON.parse(document.getElementById('ev-payload').value || '{}'); } catch (e) { return toast('Payload must be valid JSON', true); }
    call(() => Apx.v2(`/accounts/${a.id}/events`, { method: 'POST', body: { event_type: document.getElementById('ev-type').value, effective_from: document.getElementById('ev-from').value || null, effective_to: document.getElementById('ev-to').value || null, source_text: document.getElementById('ev-note').value, payload } }), 'Event added'); });
}

function v2Subs(el, a, call) {
  el.innerHTML = `<div class="card"><h3>Periodic subscription invoices</h3><p class="muted">A subscription covers the platform fee for its months — the monthly run won't bill it again (usage-based UPI and user charges still bill).</p>
    ${tbl(['Period', 'Frequency', '>Amount', 'Description', 'Status', ''], a.subscriptions.map(s => `<tr data-id="${s.id}"><td>${esc(s.period_start)} → ${esc(s.period_end)}</td><td>${esc(s.frequency)}</td><td class="right amount">${v2Money(s.amount)}</td><td>${esc(s.description)}</td><td><span class="badge ${s.status === 'raised' ? 'locked' : 'none'}">${esc(s.status)}</span></td><td>${isFinance() && s.status === 'raised' ? '<button class="ghost sub-cancel">Cancel</button>' : ''}</td></tr>`), 'No subscription invoices.')}
    ${isFinance() ? `<h4>Record a subscription invoice</h4><div class="form-grid"><label>From<input type="date" id="sb-from" /></label><label>To<input type="date" id="sb-to" /></label><label>Amount ₹<input type="number" id="sb-amt" step="any" /></label>
      <label>Frequency<select id="sb-freq"><option value="annual">annual</option><option value="half_yearly">half-yearly</option><option value="quarterly">quarterly</option></select></label><label>Description<input id="sb-desc" value="Annual Subscription" /></label></div><div class="actions-row" style="margin-top:.8rem"><button id="sb-add">Add</button></div>` : ''}</div>`;
  el.querySelectorAll('.sub-cancel').forEach(b => b.onclick = e => call(() => Apx.v2(`/subscriptions/${e.target.closest('tr').dataset.id}/status?status=cancelled`, { method: 'PUT' }), 'Cancelled'));
  document.getElementById('sb-add')?.addEventListener('click', () => call(() => Apx.v2(`/accounts/${a.id}/subscriptions`, { method: 'POST', body: { frequency: document.getElementById('sb-freq').value, period_start: document.getElementById('sb-from').value, period_end: document.getElementById('sb-to').value, amount: Number(document.getElementById('sb-amt').value), description: document.getElementById('sb-desc').value } }), 'Subscription recorded'));
}

function v2Esc(el, a, call) {
  el.innerHTML = `<div class="card"><h3>Price escalation</h3><p class="muted">When a rule falls due, a <b>draft</b> contract version with the new prices is created for Finance to review and lock. History is never changed.</p>
    ${tbl(['Applies to', 'Change', 'Next date', 'Last applied', 'Note'], a.escalations.map(r => `<tr><td>${esc(r.applies_to)}</td><td>${r.kind === 'percent' ? '+' + (r.value * 100).toFixed(1) + '%' : '+₹' + r.value}</td><td>${esc(r.next_date || '—')}</td><td>${esc(r.last_applied_date || '—')}</td><td class="muted">${esc(r.note)}</td></tr>`), 'No escalation rules.')}
    ${isFinance() ? `<h4>Add rule</h4><div class="form-grid"><label>Applies to<input id="es-to" value="all" /></label><label>Kind<select id="es-kind"><option value="percent">percent</option><option value="fixed_increment">fixed increment</option></select></label><label>Value (0.10 = 10%)<input type="number" step="any" id="es-val" value="0.10" /></label><label>Next date<input type="date" id="es-next" /></label></div>
      <div class="actions-row" style="margin-top:.8rem"><button id="es-add">Add rule</button></div>` : ''}</div>`;
  document.getElementById('es-add')?.addEventListener('click', () => call(() => Apx.v2(`/accounts/${a.id}/escalations`, { method: 'POST', body: { applies_to: document.getElementById('es-to').value, kind: document.getElementById('es-kind').value, value: Number(document.getElementById('es-val').value), next_date: document.getElementById('es-next').value } }), 'Rule added'));
}

function v2Sim(el, a) {
  const fields = [['add_money', 'Add Money ₹'], ['upi_txn_count', 'UPI transactions'], ['upi_txn_amount', 'UPI amount ₹'], ['maker_count', 'Makers'], ['admin_count', 'Admins + co-admins'], ['upi_spend_after_free', 'Spend after free txns ₹'], ['truck_count', 'Trucks'], ['driver_count', 'Drivers']];
  el.innerHTML = `<div class="card"><h3>Simulate a month</h3><p class="muted">Nothing is saved. Leave a field blank to use the stored usage snapshot; fill it in to test "what if".</p>
    <div class="form-grid"><label>Period<input type="month" id="sm-period" value="${todayStr().slice(0, 7)}" /></label>
      <label>Contract version<select id="sm-ver"><option value="">Active for that period</option>${a.contracts.map(c => `<option value="${c.id}">v${c.version} (${c.status})</option>`).join('')}</select></label>
      ${fields.map(([k, l]) => `<label>${l}<input type="number" step="any" data-u="${k}" /></label>`).join('')}</div>
    <div class="actions-row" style="margin-top:.8rem"><button id="sm-go"><i class="fa-solid fa-play"></i> Calculate</button></div><div id="sm-out"></div></div>`;
  document.getElementById('sm-go').onclick = async () => {
    const usage = {}; el.querySelectorAll('[data-u]').forEach(i => { if (i.value !== '') usage[i.dataset.u] = Number(i.value); });
    const out = document.getElementById('sm-out');
    try {
      const r = await Apx.v2(`/accounts/${a.id}/simulate`, { method: 'POST', body: { period: document.getElementById('sm-period').value, usage, contract_id: document.getElementById('sm-ver').value ? Number(document.getElementById('sm-ver').value) : null } });
      out.innerHTML = `<h4>Calculation</h4>${r.billable ? '' : `<div class="alert-bar warn"><i class="fa-solid fa-ban"></i><span>Not billable for this period (${esc(r.reason)}) — the amounts below are what the contract <i>would</i> bill.</span></div>`}
        ${r.active_days < r.days_in_month ? `<p class="muted">Pro-rata: ${r.active_days}/${r.days_in_month} days active.</p>` : ''}
        ${tbl(['Charge', 'How it was calculated', '>Amount'], r.lines.map(l => `<tr><td><b>${esc(l.label)}</b> ${l.flags.map(f => `<span class="badge ${f === 'unmodelled' ? 'error' : 'none'}">${esc(f.replace(/_/g, ' '))}</span>`).join(' ')}</td><td class="muted" style="white-space:normal">${esc(l.detail)}</td><td class="right amount">${v2Money(l.amount)}</td></tr>`).concat([`<tr class="total-row"><td>Taxable value</td><td></td><td class="right amount">${v2Money(r.subtotal)}</td></tr>`]), 'No charges.')}
        ${r.exceptions.length ? `<h4>Findings</h4><div class="findings">${r.exceptions.map(e => `<div class="finding ${e.severity === 'info' ? 'low' : e.severity}"><i class="fa-solid fa-circle-info"></i><div><span class="badge ${sevBadge[e.severity]}">${esc(e.code)}</span> ${e.blocks ? '<span class="badge error">blocks invoice</span>' : ''}<div>${esc(e.message)}</div></div></div>`).join('')}</div>` : '<div class="finding ok"><i class="fa-solid fa-circle-check"></i><div>No findings.</div></div>'}`;
    } catch (e) { out.innerHTML = `<p style="color:var(--danger)">${esc(e.message)}</p>`; }
  };
}

// ------------------------------- exceptions -------------------------------
async function v2ExceptionsTab(panel) {
  const st = state.v2ExStatus || 'open';
  const rows = await Apx.v2(`/exceptions?status=${st}`);
  panel.innerHTML = `<div class="card"><div class="actions-row"><select id="ex-st"><option value="open" ${st === 'open' ? 'selected' : ''}>Open</option><option value="all" ${st === 'all' ? 'selected' : ''}>All</option><option value="resolved" ${st === 'resolved' ? 'selected' : ''}>Resolved</option><option value="waived" ${st === 'waived' ? 'selected' : ''}>Waived</option></select></div>
    ${tbl(['Severity', 'Code', 'Account', 'Issue', 'Run', 'Status', ''], rows.map(e => `<tr data-id="${e.id}"><td><span class="badge ${sevBadge[e.severity]}">${esc(e.severity)}</span></td><td class="mono" style="font-size:.76rem">${esc(e.code)}${e.blocks ? ' <span class="badge error">blocks</span>' : ''}</td><td>${esc(e.account)}</td><td style="white-space:normal;min-width:280px">${esc(e.message)}</td><td>${e.run_id ? '#' + e.run_id : '—'}</td>
      <td><span class="badge ${e.status === 'open' ? 'draft' : 'locked'}">${esc(e.status)}</span>${e.resolved_note ? `<div class="muted" style="font-size:.72rem">${esc(e.resolved_note)}</div>` : ''}</td><td>${isFinance() && e.status === 'open' ? '<button class="ghost ex-res">Resolve</button><button class="ghost ex-waive">Waive</button>' : ''}</td></tr>`), 'No exceptions. Run billing to generate them.')}</div>`;
  panel.querySelector('#ex-st').onchange = e => { state.v2ExStatus = e.target.value; v2ExceptionsTab(panel); };
  const act = (status, needNote) => async (e) => { const note = prompt(needNote ? 'Why is this waived? (required)' : 'Resolution note (optional)', ''); if (note === null || (needNote && !note.trim())) return;
    try { await Apx.v2(`/exceptions/${e.target.closest('tr').dataset.id}/resolve`, { method: 'POST', body: { status, note } }); v2ExceptionsTab(panel); } catch (er) { toast(er.message, true); } };
  panel.querySelectorAll('.ex-res').forEach(b => b.onclick = act('resolved', false)); panel.querySelectorAll('.ex-waive').forEach(b => b.onclick = act('waived', true));
}

// ------------------------------- groups -------------------------------
async function v2GroupsTab(panel) {
  const [groups, accts, cat] = await Promise.all([Apx.v2('/groups'), Apx.v2('/accounts'), v2cat()]);
  const explain = { net_parent: 'Parent bills MAX(rate × load, minimum) less the members\' platform fees.', equal_split: 'Group minimum split equally; each member is floored at its share.', pooled: 'Minimum applied once on the members\' combined bill; any top-up goes to the parent.', per_entity_min: 'Minimum divided across a number of units; each account floored at one unit\'s share.' };
  panel.innerHTML = `${isFinance() ? '<div class="actions-row"><button id="g-new"><i class="fa-solid fa-plus"></i> New group</button></div>' : ''}<div id="g-form"></div>
    <div class="card">${tbl(['Group', 'Rule', 'Minimum', 'Rate', 'Members', ''], groups.map(g => `<tr data-id="${g.id}"><td><b>${esc(g.name)}</b></td><td>${esc(g.kind.replace(/_/g, ' '))}${g.pool_load ? ' · pooled load' : ''}</td><td class="amount">${g.group_min_amount ? v2Money(g.group_min_amount) : '—'}</td><td>${g.rate ? (g.rate * 100).toFixed(2) + '%' : '—'}</td><td style="white-space:normal">${g.members.map(m => `${esc(m.name)}${m.is_parent ? ' ★' : ''}`).join(', ')}</td><td>${isFinance() ? '<i class="fa-solid fa-pen g-edit" style="cursor:pointer"></i> <i class="fa-solid fa-trash g-del" style="cursor:pointer;color:var(--danger)"></i>' : ''}</td></tr>`), 'No groups defined.')}</div>`;
  const form = (g) => {
    const c = g || { name: '', kind: 'net_parent', group_min_amount: '', rate: '', pool_load: false, units: '', subtract: 'A', notes: '', members: [] };
    panel.querySelector('#g-form').innerHTML = `<div class="card"><h3>${g ? 'Edit' : 'New'} group</h3><div class="form-grid">
      <label>Name<input id="g-name" value="${esc(c.name)}" /></label><label>Rule<select id="g-kind">${cat.group_kinds.map(k => `<option value="${k}" ${k === c.kind ? 'selected' : ''}>${k.replace(/_/g, ' ')}</option>`).join('')}</select></label>
      <label>Group minimum ₹<input type="number" step="any" id="g-min" value="${c.group_min_amount ?? ''}" /></label><label>Load rate (0.008 = 0.8%)<input type="number" step="any" id="g-rate" value="${c.rate ?? ''}" /></label>
      <label>Units sharing the minimum<input type="number" id="g-units" value="${c.units ?? ''}" /></label><label>Net-parent: subtract members' <select id="g-sub"><option value="A" ${c.subtract === 'A' ? 'selected' : ''}>platform fee (A)</option><option value="taxable" ${c.subtract === 'taxable' ? 'selected' : ''}>whole bill</option></select></label>
      <label>Use pooled Add Money for the load leg<select id="g-pool"><option value="false" ${c.pool_load ? '' : 'selected'}>no — parent only</option><option value="true" ${c.pool_load ? 'selected' : ''}>yes — all members</option></select></label></div>
      <p class="muted" id="g-help"></p><h4>Members</h4><div id="g-members"></div><button class="ghost" id="g-addm" type="button"><i class="fa-solid fa-plus"></i> Add member</button>
      <div class="actions-row" style="margin-top:1rem"><button id="g-save">Save group</button><button class="ghost" id="g-cancel">Cancel</button></div></div>`;
    const mrow = (m) => `<div class="line-row" style="grid-template-columns:3fr 1fr 28px"><select class="gm-acct">${optList(accts, 'id', 'name', m.account_id)}</select><label class="inline-check"><input type="radio" name="gm-parent" class="gm-par" ${m.is_parent ? 'checked' : ''} /> parent</label><i class="fa-solid fa-trash l-rm gm-rm"></i></div>`;
    (c.members.length ? c.members : [{}, {}]).forEach(m => panel.querySelector('#g-members').insertAdjacentHTML('beforeend', mrow(m)));
    const wire = () => panel.querySelectorAll('.gm-rm').forEach(b => b.onclick = e => e.target.closest('.line-row').remove()); wire();
    panel.querySelector('#g-addm').onclick = () => { panel.querySelector('#g-members').insertAdjacentHTML('beforeend', mrow({})); wire(); };
    const help = () => { panel.querySelector('#g-help').textContent = explain[panel.querySelector('#g-kind').value]; }; help(); panel.querySelector('#g-kind').onchange = help;
    panel.querySelector('#g-cancel').onclick = () => { panel.querySelector('#g-form').innerHTML = ''; };
    panel.querySelector('#g-save').onclick = async () => { const n = (id) => document.getElementById(id).value === '' ? null : Number(document.getElementById(id).value);
      try { await Apx.v2(`/groups${g ? '?group_id=' + g.id : ''}`, { method: 'POST', body: { name: panel.querySelector('#g-name').value, kind: panel.querySelector('#g-kind').value, group_min_amount: n('g-min'), rate: n('g-rate'), units: n('g-units'), subtract: panel.querySelector('#g-sub').value, pool_load: panel.querySelector('#g-pool').value === 'true', notes: c.notes, members: [...panel.querySelectorAll('#g-members .line-row')].map(r => ({ account_id: Number(r.querySelector('.gm-acct').value), is_parent: r.querySelector('.gm-par').checked })) } });
        toast('Group saved'); v2GroupsTab(panel); } catch (e) { toast(e.message, true); } };
  };
  panel.querySelector('#g-new')?.addEventListener('click', () => form(null));
  panel.querySelectorAll('.g-edit').forEach(b => b.onclick = e => form(groups.find(g => String(g.id) === e.target.closest('tr').dataset.id)));
  panel.querySelectorAll('.g-del').forEach(b => b.onclick = async e => { if (!confirm('Delete this group?')) return; await Apx.v2(`/groups/${e.target.closest('tr').dataset.id}`, { method: 'DELETE' }); v2GroupsTab(panel); });
}

// ------------------------------- usage inputs -------------------------------
async function v2UsageTab(panel) {
  const period = state.v2Period || todayStr().slice(0, 7);
  const rows = await Apx.v2(`/usage/${period}`);
  const cols = [['add_money', 'Add Money'], ['upi_txn_count', 'UPI txns'], ['maker_count', 'Makers'], ['admin_count', 'Admins'], ['upi_spend_after_free', 'Spend after free'], ['truck_count', 'Trucks'], ['driver_count', 'Drivers']];
  const cell = (r, k) => r[k] === null ? '<span class="muted" title="not supplied">—</span>' : `${Number(r[k]).toLocaleString('en-IN')}${r.manual_fields.includes(k) ? ' <i class="fa-solid fa-hand" title="typed by hand" style="color:var(--warn)"></i>' : ''}`;
  panel.innerHTML = `<div class="card"><div class="actions-row"><input type="month" id="us-p" value="${period}" /><button class="ghost" id="us-go">Show</button>${['accounts', 'finance'].includes(state.user.role) ? '<button id="us-ref"><i class="fa-solid fa-rotate"></i> Pull from usage database</button>' : ''}</div>
    <p class="muted">Frozen once the month's run is approved, so a re-run always reproduces the same bill. "—" means the feed didn't supply that metric (not zero). Fields you type by hand are flagged on the invoice.</p>
    ${tbl(['Enterprise', ...cols.map(c => '>' + c[1]), 'Source', ''], rows.map(r => `<tr data-eid="${esc(r.enterprise_id)}"><td class="mono">${esc(r.enterprise_id)}</td>${cols.map(([k]) => `<td class="right amount">${cell(r, k)}</td>`).join('')}<td><span class="badge ${r.frozen ? 'locked' : 'none'}">${r.frozen ? 'frozen' : esc(r.source)}</span></td><td>${!r.frozen && ['accounts', 'finance'].includes(state.user.role) ? '<button class="ghost us-edit">Edit</button>' : ''}</td></tr>`), 'No usage for this period — pull from the database.')}</div>`;
  panel.querySelector('#us-go').onclick = () => { state.v2Period = panel.querySelector('#us-p').value; v2UsageTab(panel); };
  panel.querySelector('#us-ref')?.addEventListener('click', async () => { try { const r = await Apx.v2(`/usage/${panel.querySelector('#us-p').value}/refresh`, { method: 'POST' }); toast(`${r.enterprises} enterprises (${r.created} new, ${r.updated} updated, ${r.skipped_frozen} frozen)`); state.v2Period = panel.querySelector('#us-p').value; v2UsageTab(panel); } catch (e) { toast(e.message, true); } });
  panel.querySelectorAll('.us-edit').forEach(b => b.onclick = async e => {
    const eid = e.target.closest('tr').dataset.eid; const k = prompt(`Which field? (${cols.map(c => c[0]).join(', ')})`, 'upi_spend_after_free'); if (!k) return;
    const v = prompt(`Value for ${k} (${eid})`); if (v === null) return;
    try { await Apx.v2(`/usage/${period}/${encodeURIComponent(eid)}`, { method: 'PUT', body: { values: { [k]: v === '' ? null : Number(v) } } }); toast('Saved — flagged as typed by hand'); v2UsageTab(panel); } catch (er) { toast(er.message, true); } });
}

// ------------------------------- import -------------------------------
function v2ImportTab(panel) {
  const can = isFinance();
  panel.innerHTML = `${can ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-lock"></i><span>Only Finance can import or change terms.</span></div>'}
    <div class="grid-2">
      <div class="card"><h3><i class="fa-solid fa-file-code"></i> 1 · Rulebook (JSONL)</h3><p class="muted">One billing line per row. Charges it recognises are modelled; anything else is imported as "needs modelling" and withholds that invoice until a person models it. Safe to re-run.</p>
        <input type="file" id="im-rules" accept=".jsonl,.json,.txt" /><div class="actions-row" style="margin-top:.6rem"><label class="inline-check"><input type="checkbox" id="im-lock" checked /> lock imported contracts</label><label class="inline-check"><input type="checkbox" id="im-replace" /> replace existing</label></div>
        <button id="im-rules-go" ${can ? '' : 'disabled'}>Import rulebook</button></div>
      <div class="card"><h3><i class="fa-solid fa-link"></i> 2 · Enterprise-ID mapping (CSV)</h3><p class="muted">Imported accounts start with placeholder IDs and are withheld from billing until mapped. Download the template, fill in the platform <code>enterprise_id</code> (use <code>;</code> for clubbed enterprises), upload it.</p>
        <div class="actions-row"><a class="btn ghost" href="${Apx.v2mappingTemplateUrl()}"><i class="fa-solid fa-download"></i> Template</a><input type="file" id="im-map" accept=".csv" /></div><button id="im-map-go" ${can ? '' : 'disabled'}>Import mapping</button></div>
      <div class="card"><h3><i class="fa-solid fa-calendar-check"></i> 3 · Periodic register (CSV)</h3><p class="muted">Annual / quarterly subscription invoices. They stop the monthly run from billing the platform fee for a covered month.</p>
        <input type="file" id="im-reg" accept=".csv" /><div style="margin-top:.6rem"><button id="im-reg-go" ${can ? '' : 'disabled'}>Import register</button></div></div>
      <div class="card"><h3><i class="fa-solid fa-arrow-trend-up"></i> Apply price escalations</h3><p class="muted">Creates draft contract versions for every escalation rule due on or before the date. Review and lock each one.</p>
        <div class="actions-row"><input type="date" id="im-esc" value="${todayStr()}" /><button id="im-esc-go" ${can ? '' : 'disabled'}>Create drafts</button></div><div id="im-esc-out"></div></div></div>
    <div id="im-out"></div>`;
  const show = (title, r) => { panel.querySelector('#im-out').innerHTML = `<div class="card"><h4>${title}</h4><pre class="mono" style="white-space:pre-wrap;margin:0">${esc(JSON.stringify(r, null, 2))}</pre></div>`; };
  const upload = (btn, input, path, params, title) => document.getElementById(btn).addEventListener('click', async () => {
    const f = document.getElementById(input).files[0]; if (!f) return toast('Choose a file first', true);
    try { show(title, await Apx.v2form(path, f, typeof params === 'function' ? params() : params)); toast(`${title} complete`); } catch (e) { toast(e.message, true); } });
  upload('im-rules-go', 'im-rules', '/import/rules', () => `?lock=${panel.querySelector('#im-lock').checked}&replace=${panel.querySelector('#im-replace').checked}`, 'Rulebook import');
  upload('im-map-go', 'im-map', '/import/mapping', '', 'Mapping import');
  upload('im-reg-go', 'im-reg', '/import/register', '', 'Register import');
  panel.querySelector('#im-esc-go').addEventListener('click', async () => { try { const r = await Apx.v2('/escalations/apply', { method: 'POST', body: { through: panel.querySelector('#im-esc').value } }); show('Escalation drafts', r); toast(`${r.drafts_created.length} draft version(s) created`); } catch (e) { toast(e.message, true); } });
}

const V2_RENDERERS = { accounts: renderAccountsV2 };
