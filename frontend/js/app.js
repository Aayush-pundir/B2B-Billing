// ---------------------------------------------------------------------------
// B2B Billing Automation Platform — single-file SPA controller
// ---------------------------------------------------------------------------

const state = { user: null, route: 'dashboard' };

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'fa-gauge' },
  { id: 'clients', label: 'Clients', icon: 'fa-building' },
  { id: 'agreements', label: 'Agreements', icon: 'fa-file-lines' },
  { id: 'commercials', label: 'Commercials', icon: 'fa-sliders' },
  { id: 'billing', label: 'Billing', icon: 'fa-file-invoice-dollar' },
  { id: 'invoices', label: 'Invoices', icon: 'fa-receipt' },
  { id: 'reports', label: 'Reports', icon: 'fa-chart-column' },
  { id: 'metrics', label: 'Metrics & Add-ons', icon: 'fa-layer-group' },
  { id: 'users', label: 'Users', icon: 'fa-users' },
  { id: 'audit', label: 'Audit', icon: 'fa-clipboard-list' },
  { id: 'settings', label: 'Settings', icon: 'fa-gear' },
];

function toast(msg, isError = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

function esc(s) { return (s ?? '').toString().replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function money(n) { return '₹' + Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

// ---------------------------------------------------------------------------
// Boot / login
// ---------------------------------------------------------------------------

async function boot() {
  const me = await Apx.me().catch(() => null);
  if (me) {
    state.user = me;
    showApp();
  } else {
    showLogin();
  }
}

function showLogin() {
  document.getElementById('app').style.display = 'none';
  const el = document.getElementById('login-screen');
  el.style.display = 'flex';
  const roles = ['admin', 'accounts', 'finance', 'sales', 'viewer'];
  let selectedRole = 'accounts';
  el.innerHTML = `
    <div class="login-card">
      <h1>B2B Billing Automation</h1>
      <p>Sign in to continue</p>
      <div class="role-pills">${roles.map(r => `<div class="pill ${r === selectedRole ? 'active' : ''}" data-role="${r}">${r}</div>`).join('')}</div>
      <input type="email" id="login-email" placeholder="you@company.com" style="width:100%;margin-bottom:0.8rem;" value="admin@example.local" />
      <button id="login-btn" style="width:100%;justify-content:center;">Sign in (dev)</button>
    </div>`;
  el.querySelectorAll('.pill').forEach(p => p.addEventListener('click', () => {
    selectedRole = p.dataset.role;
    el.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
    p.classList.add('active');
  }));
  el.querySelector('#login-btn').addEventListener('click', async () => {
    const email = el.querySelector('#login-email').value.trim();
    if (!email) return toast('Enter an email', true);
    try {
      const res = await Apx.devLogin(email, selectedRole);
      state.user = res;
      el.style.display = 'none';
      showApp();
    } catch (e) { toast(e.message, true); }
  });
}

function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  renderSidebar();
  navigate(location.hash.replace('#', '') || 'dashboard');
}

function renderSidebar() {
  const sb = document.getElementById('sidebar');
  sb.innerHTML = `
    <div class="brand"><i class="fa-solid fa-coins"></i> BillingOS</div>
    <nav>${PAGES.map(p => `<a href="#${p.id}" data-page="${p.id}"><i class="fa-solid ${p.icon}"></i> ${p.label}</a>`).join('')}</nav>
    <div class="user-box">${esc(state.user.email)}<br/><span style="text-transform:capitalize">${esc(state.user.role)}</span><br/><a href="#" id="logout-link" style="color:var(--orange)">Sign out</a></div>
  `;
  sb.querySelector('#logout-link').addEventListener('click', async (e) => {
    e.preventDefault();
    await Apx.logout();
    location.reload();
  });
}

window.addEventListener('hashchange', () => navigate(location.hash.replace('#', '')));

async function navigate(page) {
  if (!PAGES.some(p => p.id === page)) page = 'dashboard';
  state.route = page;
  document.querySelectorAll('#sidebar nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  document.getElementById('topbar').innerHTML = `<h2>${PAGES.find(p => p.id === page).label}</h2><div class="bell" id="bell"><i class="fa-regular fa-bell"></i><span class="count" id="bell-count" style="display:none"></span></div>`;
  const content = document.getElementById('content');
  content.innerHTML = '<p class="muted">Loading…</p>';
  try {
    await RENDERERS[page](content);
  } catch (e) {
    content.innerHTML = `<div class="card"><p style="color:var(--danger)">${esc(e.message)}</p></div>`;
  }
  loadBell();
}

async function loadBell() {
  try {
    const alerts = await Apx.expiryAlerts();
    const count = (alerts.danger || []).length + (alerts.warn || []).length;
    const badge = document.getElementById('bell-count');
    if (badge && count > 0) { badge.style.display = 'inline'; badge.textContent = count; }
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

async function renderDashboard(content) {
  const [clients, commercials, runs, invoices, alerts] = await Promise.all([
    Apx.listClients(), Apx.listCommercials(), Apx.listRuns(), Apx.listInvoices(), Apx.expiryAlerts(),
  ]);
  const revenue = invoices.reduce((s, i) => s + i.total, 0);
  const locked = commercials.filter(c => c.status === 'locked').length;

  let alertHtml = '';
  if (alerts.danger.length) alertHtml += `<div class="alert-bar danger"><i class="fa-solid fa-triangle-exclamation"></i> ${alerts.danger.length} agreement(s) expiring within 30 days</div>`;
  if (alerts.warn.length) alertHtml += `<div class="alert-bar warn"><i class="fa-solid fa-clock"></i> ${alerts.warn.length} agreement(s) expiring in 31–60 days</div>`;

  content.innerHTML = `
    ${alertHtml}
    <div class="stat-grid">
      <div class="stat-card"><div class="value">${clients.length}</div><div class="label">Total Clients</div></div>
      <div class="stat-card"><div class="value">${locked}</div><div class="label">Locked Commercials</div></div>
      <div class="stat-card"><div class="value">${runs.length}</div><div class="label">Billing Runs</div></div>
      <div class="stat-card"><div class="value">${invoices.length}</div><div class="label">Invoices</div></div>
      <div class="stat-card"><div class="value">${money(revenue)}</div><div class="label">Total Revenue</div></div>
    </div>
    <div class="card">
      <h3>Quick Actions</h3>
      <div class="actions-row">
        <a class="btn" href="#clients"><i class="fa-solid fa-plus"></i> Add Client</a>
        <a class="btn secondary" href="#agreements"><i class="fa-solid fa-upload"></i> Upload Agreement</a>
        <a class="btn ghost" href="#billing"><i class="fa-solid fa-play"></i> Run Monthly Billing</a>
        <a class="btn ghost" href="#reports"><i class="fa-solid fa-download"></i> Export Reports</a>
      </div>
    </div>
    <div class="card">
      <h3>Recent Billing Runs</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Period</th><th>Invoices</th><th>Status</th><th>Created</th></tr></thead>
        <tbody>${runs.slice(0, 8).map(r => `<tr><td>${esc(r.period)}</td><td>${r.invoice_count}</td><td><span class="badge ${r.status}">${r.status}</span></td><td>${new Date(r.created_at).toLocaleString()}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No runs yet</td></tr>'}</tbody>
      </table></div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Clients
// ---------------------------------------------------------------------------

async function renderClients(content) {
  const clients = await Apx.listClients();
  content.innerHTML = `
    <div class="actions-row">
      <button id="add-client"><i class="fa-solid fa-plus"></i> Add Client</button>
      <a class="btn ghost" href="${Apx.clientTemplateUrl()}"><i class="fa-solid fa-download"></i> Bulk Import Template</a>
      <label class="btn secondary" style="cursor:pointer;">
        <i class="fa-solid fa-upload"></i> Bulk Import CSV
        <input type="file" id="bulk-file" accept=".csv" style="display:none" />
      </label>
      <input id="search" placeholder="Search clients…" style="margin-left:auto;min-width:220px" />
    </div>
    <div id="client-form-wrap"></div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>EnterpriseId</th><th>Name</th><th>GST</th><th>State</th><th>Frequency</th><th>Commercial</th><th>Zoho</th><th></th></tr></thead>
      <tbody id="client-rows"></tbody>
    </table></div></div>
  `;

  function drawRows(list) {
    document.getElementById('client-rows').innerHTML = list.map(c => `
      <tr data-id="${c.id}">
        <td>${esc(c.enterprise_id)}</td>
        <td>${esc(c.name)}</td>
        <td>${esc(c.gst)}</td>
        <td>${esc(c.state)} ${esc(c.state_code)}</td>
        <td>${esc(c.billing_frequency)}</td>
        <td><span class="badge ${c.commercial_status}">${c.commercial_status}</span></td>
        <td>${c.zoho_contact_id ? '<i class="fa-solid fa-check" style="color:var(--teal)"></i>' : `<i class="fa-solid fa-cloud zoho-create" style="cursor:pointer;color:var(--muted)" title="Create Zoho contact"></i>`}</td>
        <td><i class="fa-solid fa-pen edit-client" style="cursor:pointer;margin-right:0.6rem"></i><i class="fa-solid fa-trash del-client" style="cursor:pointer;color:var(--danger)"></i></td>
      </tr>`).join('') || '<tr><td colspan="8" class="muted">No clients yet — add one to get started.</td></tr>';

    content.querySelectorAll('.zoho-create').forEach(el => el.addEventListener('click', async (e) => {
      const id = e.target.closest('tr').dataset.id;
      try { await Apx.createZohoContact(id); toast('Zoho contact created'); navigate('clients'); }
      catch (err) { toast(err.message, true); }
    }));
    content.querySelectorAll('.del-client').forEach(el => el.addEventListener('click', async (e) => {
      const id = e.target.closest('tr').dataset.id;
      if (!confirm('Delete this client?')) return;
      await Apx.deleteClient(id); toast('Client deleted'); navigate('clients');
    }));
    content.querySelectorAll('.edit-client').forEach(el => el.addEventListener('click', (e) => {
      const id = e.target.closest('tr').dataset.id;
      const client = list.find(c => String(c.id) === id);
      showClientForm(client);
    }));
  }

  function showClientForm(client) {
    const wrap = document.getElementById('client-form-wrap');
    const c = client || { enterprise_id: '', name: '', gst: '', pan: '', state: '', state_code: '', billing_contact_email: '', billing_frequency: 'monthly' };
    wrap.innerHTML = `
      <div class="card">
        <h3>${client ? 'Edit' : 'New'} Client</h3>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;">
          <input id="f-eid" placeholder="EnterpriseId" value="${esc(c.enterprise_id)}" ${client ? 'disabled' : ''} />
          <input id="f-name" placeholder="Client name" value="${esc(c.name)}" />
          <input id="f-gst" placeholder="GSTIN" value="${esc(c.gst)}" />
          <input id="f-pan" placeholder="PAN" value="${esc(c.pan)}" />
          <input id="f-state" placeholder="State" value="${esc(c.state)}" />
          <input id="f-statecode" placeholder="State code (e.g. 09)" value="${esc(c.state_code)}" />
          <input id="f-email" placeholder="Billing contact email" value="${esc(c.billing_contact_email)}" />
          <select id="f-freq"><option value="monthly" ${c.billing_frequency === 'monthly' ? 'selected' : ''}>Monthly</option><option value="annual" ${c.billing_frequency === 'annual' ? 'selected' : ''}>Annual</option></select>
        </div>
        <div class="actions-row" style="margin-top:0.8rem">
          <button id="save-client">Save</button>
          <button class="ghost" id="cancel-client">Cancel</button>
        </div>
      </div>`;
    wrap.querySelector('#cancel-client').addEventListener('click', () => wrap.innerHTML = '');
    wrap.querySelector('#save-client').addEventListener('click', async () => {
      const data = {
        enterprise_id: wrap.querySelector('#f-eid').value.trim(),
        name: wrap.querySelector('#f-name').value.trim(),
        gst: wrap.querySelector('#f-gst').value.trim(),
        pan: wrap.querySelector('#f-pan').value.trim(),
        state: wrap.querySelector('#f-state').value.trim(),
        state_code: wrap.querySelector('#f-statecode').value.trim(),
        billing_contact_email: wrap.querySelector('#f-email').value.trim(),
        billing_frequency: wrap.querySelector('#f-freq').value,
      };
      if (!data.enterprise_id || !data.name) return toast('EnterpriseId and Name are required', true);
      try {
        if (client) await Apx.updateClient(client.id, data);
        else await Apx.createClient(data);
        toast('Client saved');
        navigate('clients');
      } catch (e) { toast(e.message, true); }
    });
  }

  document.getElementById('add-client').addEventListener('click', () => showClientForm(null));
  document.getElementById('bulk-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const res = await Apx.bulkImportClients(file);
      toast(`Imported ${res.created} clients (${res.skipped.length} skipped)`);
      navigate('clients');
    } catch (err) { toast(err.message, true); }
  });
  document.getElementById('search').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    drawRows(clients.filter(c => c.name.toLowerCase().includes(q) || c.enterprise_id.toLowerCase().includes(q)));
  });

  drawRows(clients);
}

// ---------------------------------------------------------------------------
// Agreements
// ---------------------------------------------------------------------------

async function renderAgreements(content) {
  const [agreements, clients] = await Promise.all([Apx.listAgreements(), Apx.listClients()]);
  const clientName = (id) => clients.find(c => c.id === id)?.name || `#${id}`;

  content.innerHTML = `
    <div class="card">
      <h3>Upload Agreement</h3>
      <p class="muted">Supports DOCX, digital or scanned PDF, PNG/JPG — including phone-camera photos.</p>
      <div style="display:flex;gap:0.6rem;flex-wrap:wrap;align-items:center;">
        <select id="up-client">${clients.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.enterprise_id)})</option>`).join('')}</select>
        <input type="date" id="up-end" title="Agreement end date" />
        <input type="file" id="up-file" accept=".docx,.pdf,.png,.jpg,.jpeg" />
        <button id="up-btn"><i class="fa-solid fa-upload"></i> Upload</button>
      </div>
    </div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Client</th><th>File</th><th>Type</th><th>End Date</th><th>Extraction</th><th></th></tr></thead>
      <tbody>${agreements.map(a => `
        <tr data-id="${a.id}">
          <td>${esc(clientName(a.client_id))}</td>
          <td>${esc(a.file_name)}</td>
          <td>${esc(a.file_type).toUpperCase()}</td>
          <td>${esc(a.end_date || '—')}</td>
          <td>${a.has_extraction ? '<span class="badge locked">extracted</span>' : '<span class="badge draft">pending</span>'}</td>
          <td><button class="ghost extract-btn">Extract</button></td>
        </tr>`).join('') || '<tr><td colspan="6" class="muted">No agreements uploaded yet</td></tr>'}</tbody>
    </table></div></div>
    <div id="extract-result"></div>
  `;

  document.getElementById('up-btn').addEventListener('click', async () => {
    const file = document.getElementById('up-file').files[0];
    if (!file) return toast('Choose a file first', true);
    const fd = new FormData();
    fd.append('client_id', document.getElementById('up-client').value);
    fd.append('end_date', document.getElementById('up-end').value || '');
    fd.append('file', file);
    try {
      await Apx.uploadAgreement(fd);
      toast('Agreement uploaded');
      navigate('agreements');
    } catch (e) { toast(e.message, true); }
  });

  content.querySelectorAll('.extract-btn').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    btn.textContent = 'Extracting…'; btn.disabled = true;
    try {
      const result = await Apx.extractAgreement(id);
      const confClass = result.overall_confidence < 60 ? 'confidence-low' : '';
      document.getElementById('extract-result').innerHTML = `
        <div class="card">
          <h3>Extraction Result <span class="${confClass}">(confidence ${result.overall_confidence}%)</span></h3>
          ${result.escalation_pct_annual ? `<p class="muted">Escalation clause detected: ${result.escalation_pct_annual}% / year</p>` : ''}
          <div class="table-wrap"><table>
            <thead><tr><th>Label</th><th>Type</th><th>Detail</th></tr></thead>
            <tbody>${result.rules.map(r => `<tr><td>${esc(r.label)}</td><td>${esc(r.rate_type)}</td><td class="mono">${esc(JSON.stringify(r))}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">No rules recognised — edit manually in Commercials.</td></tr>'}</tbody>
          </table></div>
          <p class="muted">Saved as a DRAFT commercial for this client — review &amp; lock it on the <a href="#commercials">Commercials</a> page.</p>
        </div>`;
      navigate('agreements');
    } catch (err) {
      toast(err.message, true);
      btn.textContent = 'Extract'; btn.disabled = false;
    }
  }));
}

// ---------------------------------------------------------------------------
// Commercials — slab builder
// ---------------------------------------------------------------------------

const RATE_TYPES = ['percent', 'flat', 'per_unit', 'hybrid', 'annual_per_unit', 'addon'];
const METRICS_LIST = ['loading', 'upi_transactions', 'admin_users', 'spender_users', 'total_users', 'trucks'];

function ruleRowHtml(rule, idx) {
  rule = rule || { label: '', metric: 'loading', rate_type: 'percent', slab_mode: 'cliff', rate_pct: 0, floor_amount: 0 };
  return `
    <div class="rule-row" data-idx="${idx}">
      <input class="r-label" placeholder="Label" value="${esc(rule.label || '')}" style="width:140px" />
      <select class="r-metric">${METRICS_LIST.map(m => `<option ${rule.metric === m ? 'selected' : ''}>${m}</option>`).join('')}</select>
      <select class="r-type">${RATE_TYPES.map(t => `<option ${rule.rate_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select>
      <input class="r-json" placeholder='{"rate_pct":1.5,"floor_amount":5000}' value='${esc(JSON.stringify(rule))}' style="flex:1;min-width:260px" />
      <i class="fa-solid fa-trash rm-rule" style="cursor:pointer;color:var(--danger)"></i>
    </div>`;
}

async function renderCommercials(content) {
  const [commercials, clients] = await Promise.all([Apx.listCommercials(), Apx.listClients()]);
  content.innerHTML = `
    <div class="actions-row">
      <select id="cm-client">${clients.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.enterprise_id)})</option>`).join('')}</select>
      <button id="cm-load" class="ghost">Load Commercial</button>
    </div>
    <div id="cm-editor"></div>
    <div class="card">
      <h3>All Commercials</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Client</th><th>Frequency</th><th>Rules</th><th>Status</th><th>Version</th></tr></thead>
        <tbody>${commercials.map(c => `<tr><td>${esc(clients.find(x => x.id === c.client_id)?.name || c.client_id)}</td><td>${esc(c.billing_frequency)}</td><td>${(c.rules || []).length}</td><td><span class="badge ${c.status}">${c.status}</span></td><td>v${c.version_no}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No commercials yet</td></tr>'}</tbody>
      </table></div>
    </div>
  `;

  function loadEditor(clientId) {
    const existing = commercials.find(c => String(c.client_id) === String(clientId));
    const editor = document.getElementById('cm-editor');
    const rules = existing ? existing.rules : [];
    const locked = existing && existing.status === 'locked';

    editor.innerHTML = `
      <div class="card">
        <div class="flex-between">
          <h3>Commercial ${existing ? `(v${existing.version_no}, ${existing.status})` : '(new)'}</h3>
          <div>
            ${existing ? `<button class="ghost" id="cm-history">History</button>` : ''}
            ${existing && existing.status === 'locked' ? `<button class="ghost" id="cm-unlock">Unlock</button><button class="ghost" id="cm-escalate">Escalate %</button>` : ''}
          </div>
        </div>
        <label>Billing frequency:
          <select id="cm-freq" ${locked ? 'disabled' : ''}>
            <option value="monthly" ${(existing?.billing_frequency || 'monthly') === 'monthly' ? 'selected' : ''}>Monthly</option>
            <option value="annual" ${existing?.billing_frequency === 'annual' ? 'selected' : ''}>Annual</option>
          </select>
        </label>
        <div id="rule-list" style="margin-top:0.8rem">${rules.map((r, i) => ruleRowHtml(r, i)).join('')}</div>
        <div class="actions-row">
          <button class="ghost" id="add-rule" ${locked ? 'disabled' : ''}>+ Add rule</button>
          <button id="save-commercial" ${locked ? 'disabled' : ''}>Save Draft</button>
          ${existing && existing.status !== 'locked' ? `<button class="secondary" id="lock-commercial">Lock Commercial</button>` : ''}
        </div>
        <p class="muted" style="font-size:0.78rem">Each rule's JSON field mirrors the engine schema — edit metric/rate_type/rate_pct/floor_amount/unit_rate/tiers/etc directly for full flexibility.</p>
        <div id="cm-history-box"></div>
      </div>`;

    function bindRuleRemovers() {
      editor.querySelectorAll('.rm-rule').forEach(el => el.addEventListener('click', (e) => {
        e.target.closest('.rule-row').remove();
      }));
    }
    bindRuleRemovers();

    editor.querySelector('#add-rule')?.addEventListener('click', () => {
      const list = editor.querySelector('#rule-list');
      const div = document.createElement('div');
      div.innerHTML = ruleRowHtml(null, list.children.length);
      list.appendChild(div.firstElementChild);
      bindRuleRemovers();
    });

    function collectRules() {
      return [...editor.querySelectorAll('.rule-row')].map(row => {
        try {
          const parsed = JSON.parse(row.querySelector('.r-json').value);
          parsed.label = row.querySelector('.r-label').value;
          parsed.metric = row.querySelector('.r-metric').value;
          parsed.rate_type = row.querySelector('.r-type').value;
          return parsed;
        } catch (e) {
          throw new Error('Invalid rule JSON: ' + row.querySelector('.r-json').value);
        }
      });
    }

    editor.querySelector('#save-commercial')?.addEventListener('click', async () => {
      try {
        const rules = collectRules();
        await Apx.saveCommercial({ client_id: Number(clientId), billing_frequency: editor.querySelector('#cm-freq').value, rules });
        toast('Commercial saved as draft');
        navigate('commercials');
      } catch (e) { toast(e.message, true); }
    });

    editor.querySelector('#lock-commercial')?.addEventListener('click', async () => {
      try {
        const rules = collectRules();
        const saved = await Apx.saveCommercial({ client_id: Number(clientId), billing_frequency: editor.querySelector('#cm-freq').value, rules });
        await Apx.lockCommercial(saved.id);
        toast('Commercial locked');
        navigate('commercials');
      } catch (e) { toast(e.message, true); }
    });

    editor.querySelector('#cm-unlock')?.addEventListener('click', async () => {
      await Apx.unlockCommercial(existing.id);
      toast('Unlocked for editing');
      navigate('commercials');
    });

    editor.querySelector('#cm-escalate')?.addEventListener('click', async () => {
      const pct = prompt('Escalate all rates & floors by what %?', '10');
      if (!pct) return;
      try {
        await Apx.escalateCommercial(existing.id, Number(pct));
        toast(`Escalated by ${pct}% — commercial unlocked for review`);
        navigate('commercials');
      } catch (e) { toast(e.message, true); }
    });

    editor.querySelector('#cm-history')?.addEventListener('click', async () => {
      const versions = await Apx.commercialVersions(existing.id);
      document.getElementById('cm-history-box').innerHTML = `
        <h4>Version History</h4>
        <div class="table-wrap"><table>
          <thead><tr><th>Version</th><th>Reason</th><th>By</th><th>Date</th></tr></thead>
          <tbody>${versions.map(v => `<tr><td>v${v.version_no}</td><td>${esc(v.snapshot_reason)}</td><td>${esc(v.created_by)}</td><td>${new Date(v.created_at).toLocaleString()}</td></tr>`).join('')}</tbody>
        </table></div>`;
    });
  }

  document.getElementById('cm-load').addEventListener('click', () => loadEditor(document.getElementById('cm-client').value));
  if (clients.length) loadEditor(clients[0].id);
}

// ---------------------------------------------------------------------------
// Billing — monthly run
// ---------------------------------------------------------------------------

async function renderBilling(content) {
  const runs = await Apx.listRuns();
  const today = new Date();
  const defaultPeriod = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;

  content.innerHTML = `
    <div class="card">
      <h3>Run Monthly Billing</h3>
      <p class="muted">Upload all three CSVs — Loading, UPI Transactions, Users — for the period.</p>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.6rem;max-width:640px;">
        <label>Period<input type="month" id="b-period" value="${defaultPeriod}" /></label>
        <label>TDS Rate %<input type="number" id="b-tds" value="0" step="0.1" /></label>
        <label>Loading CSV <a href="${Apx.templateUrl('loading')}">template</a><input type="file" id="b-loading" accept=".csv" /></label>
        <label>UPI CSV <a href="${Apx.templateUrl('upi')}">template</a><input type="file" id="b-upi" accept=".csv" /></label>
        <label>Users CSV <a href="${Apx.templateUrl('users')}">template</a><input type="file" id="b-users" accept=".csv" /></label>
      </div>
      <div class="actions-row"><button id="b-run">Calculate Invoices</button></div>
    </div>
    <div id="b-result"></div>
    <div class="card">
      <h3>Past Runs</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Period</th><th>Invoices</th><th>TDS %</th><th>Status</th><th></th></tr></thead>
        <tbody>${runs.map(r => `<tr data-id="${r.id}"><td>${esc(r.period)}</td><td>${r.invoice_count}</td><td>${r.tds_rate}</td><td><span class="badge ${r.status}">${r.status}</span></td><td><button class="ghost view-run">View</button></td></tr>`).join('') || '<tr><td colspan="5" class="muted">No runs yet</td></tr>'}</tbody>
      </table></div>
    </div>
    <div id="run-detail"></div>
  `;

  document.getElementById('b-run').addEventListener('click', async () => {
    const loading = document.getElementById('b-loading').files[0];
    const upi = document.getElementById('b-upi').files[0];
    const users = document.getElementById('b-users').files[0];
    if (!loading || !upi || !users) return toast('All three CSVs are required', true);
    const period = document.getElementById('b-period').value;
    const fd = new FormData();
    fd.append('period', period);
    fd.append('tds_rate', document.getElementById('b-tds').value || '0');
    fd.append('confirm_annual', '');
    fd.append('loading_file', loading);
    fd.append('upi_file', upi);
    fd.append('users_file', users);
    try {
      const res = await Apx.calculateBilling(fd);
      document.getElementById('b-result').innerHTML = `
        <div class="card">
          <h3>Run #${res.run_id} — ${res.invoices.length} invoice(s) generated</h3>
          ${res.unknown_enterprise_ids.length ? `<div class="alert-bar warn">Unknown EnterpriseIds in CSVs (not registered as clients): ${res.unknown_enterprise_ids.join(', ')}</div>` : ''}
          ${res.skipped.length ? `<h4>Skipped</h4><div class="table-wrap"><table><thead><tr><th>EnterpriseId</th><th>Reason</th></tr></thead><tbody>${res.skipped.map(s => `<tr><td>${esc(s.enterprise_id)}</td><td>${esc(s.reason)}</td></tr>`).join('')}</tbody></table></div>` : ''}
        </div>`;
      viewRun(res.run_id);
      navigate('billing');
    } catch (e) { toast(e.message, true); }
  });

  content.querySelectorAll('.view-run').forEach(btn => btn.addEventListener('click', (e) => {
    viewRun(e.target.closest('tr').dataset.id);
  }));

  async function viewRun(id) {
    const run = await Apx.runDetail(id);
    const box = document.getElementById('run-detail');
    box.innerHTML = `
      <div class="card">
        <div class="flex-between"><h3>Run ${run.period} — ${run.invoices.length} invoices</h3>
          <button class="secondary" id="push-zoho">Push to Zoho Books</button>
        </div>
        ${run.invoices.map(inv => `
          <div class="card" style="background:var(--cream)">
            <div class="flex-between"><strong>${esc(inv.invoice_no)} — ${esc(inv.client_name)} (${esc(inv.enterprise_id)})</strong><span class="badge ${inv.status}">${inv.status}</span></div>
            <div class="table-wrap"><table>
              <thead><tr><th>Line</th><th>Qty</th><th>Detail</th><th class="right">Amount</th></tr></thead>
              <tbody>${inv.lines.map(l => `<tr><td>${esc(l.label)}</td><td>${l.qty}</td><td class="muted">${esc(l.calc_detail)}</td><td class="right amount">${money(l.amount)}</td></tr>`).join('')}</tbody>
            </table></div>
            <p class="right">Subtotal: ${money(inv.subtotal)} · ${esc(inv.gst_type)}: ${money(inv.gst)} · TDS: ${money(inv.tds)} · <strong>Total: ${money(inv.total)}</strong></p>
          </div>`).join('') || '<p class="muted">No invoices in this run.</p>'}
      </div>`;
    box.querySelector('#push-zoho').addEventListener('click', async () => {
      try {
        const res = await Apx.pushZoho(id);
        toast(`Pushed ${res.pushed.length} invoice(s)${res.failed.length ? `, ${res.failed.length} failed` : ''}`);
        viewRun(id);
      } catch (e) { toast(e.message, true); }
    });
  }
}

// ---------------------------------------------------------------------------
// Invoices
// ---------------------------------------------------------------------------

async function renderInvoices(content) {
  const invoices = await Apx.listInvoices();
  content.innerHTML = `
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Invoice No</th><th>Client</th><th>Period</th><th class="right">Subtotal</th><th class="right">GST</th><th class="right">TDS</th><th class="right">Total</th><th>Status</th></tr></thead>
      <tbody>${invoices.map(i => `<tr><td>${esc(i.invoice_no)}</td><td>${esc(i.client_name)}</td><td>${esc(i.period)}</td><td class="right amount">${money(i.subtotal)}</td><td class="right amount">${money(i.gst)}</td><td class="right amount">${money(i.tds)}</td><td class="right amount">${money(i.total)}</td><td><span class="badge ${i.status}">${i.status}</span></td></tr>`).join('') || '<tr><td colspan="8" class="muted">No invoices yet</td></tr>'}</tbody>
    </table></div></div>
  `;
}

// ---------------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------------

async function renderReports(content) {
  content.innerHTML = `
    <div class="card">
      <h3>Excel Reports</h3>
      <div class="actions-row">
        <a class="btn" href="${Apx.reportExportUrl('invoice_register')}"><i class="fa-solid fa-file-excel"></i> Invoice Register</a>
        <a class="btn secondary" href="${Apx.reportExportUrl('revenue_summary')}"><i class="fa-solid fa-file-excel"></i> Revenue Summary</a>
        <a class="btn ghost" href="${Apx.reportExportUrl('client_master')}"><i class="fa-solid fa-file-excel"></i> Client Master</a>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Metrics & Add-ons
// ---------------------------------------------------------------------------

async function renderMetrics(content) {
  const [metrics, addons] = await Promise.all([Apx.listMetrics(), Apx.listAddons()]);
  content.innerHTML = `
    <div class="card">
      <h3>Metrics</h3>
      <div class="table-wrap"><table><thead><tr><th>Code</th><th>Label</th><th>Basis</th><th>Active</th></tr></thead>
      <tbody>${metrics.map(m => `<tr><td>${esc(m.code)}</td><td>${esc(m.label)}</td><td>${esc(m.basis)}</td><td>${m.active ? 'Yes' : 'No'}</td></tr>`).join('')}</tbody></table></div>
      <div class="actions-row">
        <input id="m-code" placeholder="code" style="width:120px" /><input id="m-label" placeholder="label" style="width:160px" />
        <select id="m-basis"><option>amount</option><option>count</option></select>
        <button id="m-add">Add Metric</button>
      </div>
    </div>
    <div class="card">
      <h3>Add-ons</h3>
      <div class="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>Default Rate Type</th><th>Active</th></tr></thead>
      <tbody>${addons.map(a => `<tr><td>${esc(a.code)}</td><td>${esc(a.name)}</td><td>${esc(a.default_rate_type)}</td><td>${a.active ? 'Yes' : 'No'}</td></tr>`).join('')}</tbody></table></div>
      <div class="actions-row">
        <input id="a-code" placeholder="code" style="width:120px" /><input id="a-name" placeholder="name" style="width:160px" />
        <select id="a-rate"><option>flat</option><option>per_unit</option><option>annual</option></select>
        <button id="a-add">Add Add-on</button>
      </div>
    </div>
  `;
  document.getElementById('m-add').addEventListener('click', async () => {
    await Apx.createMetric({ code: document.getElementById('m-code').value, label: document.getElementById('m-label').value, basis: document.getElementById('m-basis').value });
    navigate('metrics');
  });
  document.getElementById('a-add').addEventListener('click', async () => {
    await Apx.createAddon({ code: document.getElementById('a-code').value, name: document.getElementById('a-name').value, default_rate_type: document.getElementById('a-rate').value });
    navigate('metrics');
  });
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

async function renderUsers(content) {
  let users = [];
  try { users = await Apx.listUsers(); } catch (e) { content.innerHTML = `<div class="card"><p class="muted">Admin access required.</p></div>`; return; }
  content.innerHTML = `
    <div class="card">
      <div class="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Provider</th><th>Last Login</th><th></th></tr></thead>
      <tbody>${users.map(u => `<tr data-id="${u.id}"><td>${esc(u.email)}</td><td>${esc(u.role)}</td><td>${esc(u.provider)}</td><td>${u.last_login ? new Date(u.last_login).toLocaleString() : '—'}</td><td><i class="fa-solid fa-trash del-user" style="cursor:pointer;color:var(--danger)"></i></td></tr>`).join('')}</tbody></table></div>
      <div class="actions-row">
        <input id="u-email" placeholder="email" />
        <select id="u-role"><option>admin</option><option>accounts</option><option>finance</option><option>sales</option><option selected>viewer</option></select>
        <button id="u-add">Add User</button>
      </div>
    </div>`;
  content.querySelectorAll('.del-user').forEach(el => el.addEventListener('click', async (e) => {
    await Apx.deleteUser(e.target.closest('tr').dataset.id); navigate('users');
  }));
  document.getElementById('u-add').addEventListener('click', async () => {
    try {
      await Apx.createUser({ email: document.getElementById('u-email').value, role: document.getElementById('u-role').value });
      navigate('users');
    } catch (e) { toast(e.message, true); }
  });
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

async function renderAudit(content) {
  const rows = await Apx.auditLog();
  content.innerHTML = `
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>When</th><th>User</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td>${new Date(r.ts).toLocaleString()}</td><td>${esc(r.user)}</td><td>${esc(r.action)}</td><td>${esc(r.entity)}</td><td class="muted">${esc(r.detail)}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No activity yet</td></tr>'}</tbody>
    </table></div></div>
  `;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function renderSettings(content) {
  const [settings, providers, health] = await Promise.all([Apx.getSettings(), Apx.providers(), Apx.health()]);
  content.innerHTML = `
    <div class="card">
      <h3>Organisation Settings</h3>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.6rem;max-width:640px;">
        ${Object.entries(settings).map(([k, v]) => `<label>${k}<input class="s-field" data-key="${k}" value="${esc(v)}" /></label>`).join('')}
      </div>
      <div class="actions-row"><button id="save-settings">Save Settings</button></div>
    </div>
    <div class="card">
      <h3>Integration Status</h3>
      <ul>
        <li>Microsoft OAuth: ${providers.microsoft ? '<span style="color:var(--teal)">configured</span>' : '<span class="muted">not configured</span>'}</li>
        <li>Google OAuth: ${providers.google ? '<span style="color:var(--teal)">configured</span>' : '<span class="muted">not configured</span>'}</li>
        <li>Zoho Books: ${health.zoho ? '<span style="color:var(--teal)">configured</span>' : '<span class="muted">simulation mode (no credentials set)</span>'}</li>
        <li>Database: ${health.db ? '<span style="color:var(--teal)">ok</span>' : '<span style="color:var(--danger)">error</span>'}</li>
      </ul>
    </div>
  `;
  document.getElementById('save-settings').addEventListener('click', async () => {
    const values = {};
    content.querySelectorAll('.s-field').forEach(el => values[el.dataset.key] = el.value);
    try { await Apx.putSettings(values); toast('Settings saved'); } catch (e) { toast(e.message, true); }
  });
}

const RENDERERS = {
  dashboard: renderDashboard,
  clients: renderClients,
  agreements: renderAgreements,
  commercials: renderCommercials,
  billing: renderBilling,
  invoices: renderInvoices,
  reports: renderReports,
  metrics: renderMetrics,
  users: renderUsers,
  audit: renderAudit,
  settings: renderSettings,
};

boot();
