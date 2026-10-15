// ---------------------------------------------------------------------------
// B2B Billing Automation Platform — single-file SPA controller
// ---------------------------------------------------------------------------

const state = { user: null, route: 'dashboard' };

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'fa-gauge', group: 'Overview', sub: 'Your billing pipeline at a glance' },
  { id: 'clients', label: 'Customers', icon: 'fa-building', group: 'Setup', sub: 'Customer master data — identity, tax, contacts and billing preferences' },
  { id: 'agreements', label: 'Agreements', icon: 'fa-file-lines', group: 'Setup', sub: 'Upload contracts and extract commercial terms' },
  { id: 'commercials', label: 'Commercials', icon: 'fa-sliders', group: 'Setup', sub: 'Billing terms — amended by Finance only, via manual entry, agreements or Excel' },
  { id: 'billing', label: 'Billing', icon: 'fa-file-invoice-dollar', group: 'Billing', sub: 'Run the monthly billing cycle from live usage data' },
  { id: 'invoices', label: 'Invoices', icon: 'fa-receipt', group: 'Billing', sub: 'Issue, send and track invoices' },
  { id: 'receivables', label: 'Receivables', icon: 'fa-scale-balanced', group: 'Collections', sub: 'Outstanding balances and aging' },
  { id: 'reconciliation', label: 'Reconciliation', icon: 'fa-money-check-dollar', group: 'Collections', sub: 'Match bank payments to invoices' },
  { id: 'crm', label: 'CRM Sync', icon: 'fa-rotate', group: 'Collections', sub: 'Keep client identity in sync with your CRM' },
  { id: 'reports', label: 'Reports', icon: 'fa-chart-column', group: 'Insights', sub: 'Export billing and collections reports' },
  { id: 'metrics', label: 'Metrics & Add-ons', icon: 'fa-layer-group', group: 'Insights', sub: 'Usage metrics and add-on line items' },
  { id: 'users', label: 'Users', icon: 'fa-users', group: 'Admin', sub: 'Team members and roles' },
  { id: 'audit', label: 'Audit', icon: 'fa-clipboard-list', group: 'Admin', sub: 'Every change, with who and when' },
  { id: 'settings', label: 'Settings', icon: 'fa-gear', group: 'Admin', sub: 'Organisation, tax and delivery settings' },
];

function toast(msg, isError = false) {
  const host = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.innerHTML = `<i class="fa-solid ${isError ? 'fa-circle-exclamation' : 'fa-circle-check'}"></i><span></span>`;
  t.querySelector('span').textContent = msg;
  host.appendChild(t);
  setTimeout(() => t.remove(), 3800);
}

function esc(s) { return (s ?? '').toString().replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
const isFinance = () => state.user && state.user.role === 'finance';
const canEditClients = () => state.user && ['admin', 'accounts', 'finance'].includes(state.user.role);
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
    <div class="login-wrap">
      <section class="login-hero">
        <div class="brand-lg"><span class="logo-mark"><i class="fa-solid fa-coins"></i></span> BillingOS</div>
        <div>
          <h2>From agreement to cash, on autopilot.</h2>
          <p>Extract commercial terms, run slab billing, issue GST-ready invoices and reconcile bank payments — all in one place.</p>
          <div class="hero-steps"><span>Agreements</span><span>Billing</span><span>Invoices</span><span>Reconciliation</span><span>Aging</span></div>
        </div>
        <div class="hero-foot">Offline-first · No external AI costs · Human-in-the-loop approvals</div>
      </section>
      <section class="login-side">
        <div class="login-card">
          <h1>Welcome back</h1>
          <p>Sign in to your billing workspace.</p>
          <label>Role</label>
          <div class="role-pills">${roles.map(r => `<div class="pill ${r === selectedRole ? 'active' : ''}" data-role="${r}">${r}</div>`).join('')}</div>
          <label for="login-email">Email</label>
          <input type="email" id="login-email" placeholder="you@company.com" value="admin@example.local" />
          <button id="login-btn" class="btn-lg">Sign in <i class="fa-solid fa-arrow-right"></i></button>
          <div class="login-note">Dev sign-in — SSO is available once OAuth is configured.</div>
        </div>
      </section>
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
  let lastGroup = '';
  const links = PAGES.map(p => {
    const head = p.group !== lastGroup ? `<div class="nav-group">${p.group}</div>` : '';
    lastGroup = p.group;
    return `${head}<a href="#${p.id}" data-page="${p.id}"><i class="fa-solid ${p.icon}"></i> ${p.label}</a>`;
  }).join('');
  sb.innerHTML = `
    <div class="brand"><span class="logo-mark"><i class="fa-solid fa-coins"></i></span> BillingOS</div>
    <nav>${links}</nav>
    <div class="user-box">
      <div class="avatar">${esc(state.user.email.charAt(0))}</div>
      <div class="user-meta"><div class="email">${esc(state.user.email)}</div><div class="role">${esc(state.user.role)}</div></div>
      <button class="icon-btn" id="logout-link" title="Sign out" aria-label="Sign out"><i class="fa-solid fa-arrow-right-from-bracket"></i></button>
    </div>`;
  sb.querySelector('#logout-link').addEventListener('click', async () => {
    await Apx.logout();
    location.reload();
  });
  sb.querySelectorAll('nav a').forEach(a => a.addEventListener('click', () => document.body.classList.remove('nav-open')));
}

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('theme', t); } catch (e) { /* ignore */ }
}
function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  const i = document.querySelector('#theme-btn i');
  if (i) i.className = 'fa-solid ' + (document.documentElement.dataset.theme === 'dark' ? 'fa-sun' : 'fa-moon');
}

window.addEventListener('hashchange', () => navigate(location.hash.replace('#', '')));

async function navigate(page) {
  if (!PAGES.some(p => p.id === page)) page = 'dashboard';
  state.route = page;
  document.querySelectorAll('#sidebar nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  const meta = PAGES.find(p => p.id === page);
  const dark = document.documentElement.dataset.theme === 'dark';
  document.getElementById('topbar').innerHTML = `
    <button class="icon-btn menu-btn" id="menu-btn" aria-label="Menu"><i class="fa-solid fa-bars"></i></button>
    <div class="page-title"><h2>${meta.label}</h2><div class="sub">${meta.sub}</div></div>
    <div class="topbar-actions">
      <button class="icon-btn" id="theme-btn" title="Toggle theme" aria-label="Toggle theme"><i class="fa-solid ${dark ? 'fa-sun' : 'fa-moon'}"></i></button>
      <div class="bell" id="bell"><button class="icon-btn" title="Agreement alerts" aria-label="Alerts"><i class="fa-regular fa-bell"></i></button><span class="count" id="bell-count"></span></div>
    </div>`;
  document.getElementById('theme-btn').addEventListener('click', toggleTheme);
  document.getElementById('menu-btn').addEventListener('click', () => document.body.classList.toggle('nav-open'));
  document.getElementById('bell').addEventListener('click', () => { location.hash = 'agreements'; });
  const content = document.getElementById('content');
  content.innerHTML = '<div class="skeleton-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div><div class="skeleton block"></div>';
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
    if (badge && count > 0) { badge.style.display = 'grid'; badge.textContent = count; }
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
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-building"></i></div><div class="value">${clients.length}</div><div class="label">Total Clients</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-lock"></i></div><div class="value">${locked}</div><div class="label">Locked Commercials</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-play"></i></div><div class="value">${runs.length}</div><div class="label">Billing Runs</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-receipt"></i></div><div class="value">${invoices.length}</div><div class="label">Invoices</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-indian-rupee-sign"></i></div><div class="value">${money(revenue)}</div><div class="label">Total Revenue</div></div>
    </div>
    <div class="card">
      <h3>Quick actions</h3>
      <div class="quick-grid">
        <a class="quick-tile" href="#clients"><span class="qi"><i class="fa-solid fa-plus"></i></span><div><b>Add client</b><span>Create or import accounts</span></div></a>
        <a class="quick-tile" href="#agreements"><span class="qi"><i class="fa-solid fa-upload"></i></span><div><b>Upload agreement</b><span>Auto-extract terms</span></div></a>
        <a class="quick-tile" href="#billing"><span class="qi"><i class="fa-solid fa-play"></i></span><div><b>Run monthly billing</b><span>Upload usage CSVs</span></div></a>
        <a class="quick-tile" href="#reports"><span class="qi"><i class="fa-solid fa-download"></i></span><div><b>Export reports</b><span>Billing &amp; collections</span></div></a>
      </div>
    </div>
    <div class="card">
      <h3>Recent billing runs</h3>
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
      ${canEditClients() ? `<button id="add-client"><i class="fa-solid fa-plus"></i> Add customer</button>
      <a class="btn ghost" href="${Apx.clientTemplateUrl()}"><i class="fa-solid fa-download"></i> Template</a>
      <label class="btn secondary" style="cursor:pointer;">
        <i class="fa-solid fa-file-excel"></i> Bulk import (Excel / CSV)
        <input type="file" id="bulk-file" accept=".csv,.xlsx,.xlsm" style="display:none" />
      </label>
      <label class="inline-check"><input type="checkbox" id="bulk-update" /> update existing</label>` : ''}
      <a class="btn ghost" href="${Apx.clientExportUrl()}"><i class="fa-solid fa-file-export"></i> Export</a>
      <input id="search" placeholder="Search customers…" style="margin-left:auto;min-width:220px" />
    </div>
    <div id="client-form-wrap"></div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>EnterpriseId</th><th>Customer</th><th>GST</th><th>State</th><th>Frequency</th><th>Status</th><th>Terms</th><th>Zoho</th><th></th></tr></thead>
      <tbody id="client-rows"></tbody>
    </table></div></div>
  `;

  function drawRows(list) {
    document.getElementById('client-rows').innerHTML = list.map(c => `
      <tr data-id="${c.id}">
        <td>${esc(c.enterprise_id)}</td>
        <td><b>${esc(c.name)}</b>${c.contact_name || c.billing_contact_email ? `<div class="muted" style="font-size:.75rem">${esc(c.contact_name || c.billing_contact_email)}</div>` : ''}</td>
        <td>${esc(c.gst)}</td>
        <td>${esc(c.state)} ${esc(c.state_code)}</td>
        <td>${esc(c.billing_frequency)}</td>
        <td><span class="badge ${c.status === 'active' ? 'locked' : c.status === 'blocked' ? 'error' : 'none'}">${esc(c.status || 'active')}</span></td>
        <td><span class="badge ${c.commercial_status}">${c.commercial_status}</span></td>
        <td>${c.zoho_contact_id ? '<i class="fa-solid fa-check" style="color:var(--teal)"></i>' : `<i class="fa-solid fa-cloud zoho-create" style="cursor:pointer;color:var(--muted)" title="Create Zoho contact"></i>`}</td>
        <td><i class="fa-solid fa-file-invoice statement-client" style="cursor:pointer;margin-right:0.6rem" title="Statement of account"></i>${canEditClients() ? `<i class="fa-solid fa-pen edit-client" style="cursor:pointer;margin-right:0.6rem"></i><i class="fa-solid fa-trash del-client" style="cursor:pointer;color:var(--danger)"></i>` : ''}</td>
      </tr>`).join('') || '<tr><td colspan="9" class="muted">No customers yet — add one or bulk-import from Excel.</td></tr>';

    content.querySelectorAll('.statement-client').forEach(el => el.addEventListener('click', async (e) => {
      const id = e.target.closest('tr').dataset.id;
      const s = await Apx.clientStatement(id);
      const wrap = document.getElementById('client-form-wrap');
      wrap.innerHTML = `
        <div class="card">
          <div class="flex-between"><h3>Statement — ${esc(s.client.name)}</h3><i class="fa-solid fa-xmark close-statement" style="cursor:pointer"></i></div>
          <p>Billed: ${money(s.total_billed)} · Collected: ${money(s.total_collected)} · <strong>Outstanding: ${money(s.outstanding)}</strong></p>
          <div class="table-wrap"><table>
            <thead><tr><th>Date</th><th>Type</th><th>Ref</th><th class="right">Debit</th><th class="right">Credit</th><th class="right">Balance</th></tr></thead>
            <tbody>${s.entries.map(en => `<tr><td>${esc((en.date || '').slice(0, 10))}</td><td>${esc(en.type)}</td><td>${esc(en.ref)}</td><td class="right amount">${en.debit ? money(en.debit) : ''}</td><td class="right amount">${en.credit ? money(en.credit) : ''}</td><td class="right amount">${money(en.running_balance)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No activity yet</td></tr>'}</tbody>
          </table></div>
        </div>`;
      wrap.querySelector('.close-statement').addEventListener('click', () => wrap.innerHTML = '');
    }));

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

  const FORM = [
    ['Identity', [
      ['enterprise_id', 'Enterprise ID *'], ['name', 'Display name *'], ['legal_name', 'Legal name'], ['trade_name', 'Trade name'],
      ['customer_type', 'Type', ['business', 'individual']], ['status', 'Status', ['active', 'inactive', 'blocked']],
    ]],
    ['Tax', [
      ['gst_treatment', 'GST treatment', ['registered', 'unregistered', 'composition', 'sez', 'overseas']], ['gst', 'GSTIN'], ['pan', 'PAN'], ['tan', 'TAN'],
      ['cin', 'CIN'], ['msme_no', 'MSME / Udyam no.'],
    ]],
    ['Address', [
      ['address_line1', 'Address line 1'], ['address_line2', 'Address line 2'], ['city', 'City'], ['state', 'State'], ['state_code', 'State code (e.g. 09)'],
      ['pincode', 'PIN code'], ['country', 'Country'],
    ]],
    ['Contacts', [
      ['contact_name', 'Contact person'], ['contact_phone', 'Phone'], ['billing_contact_email', 'Billing email'], ['cc_emails', 'CC emails (comma separated)'],
    ]],
    ['Billing preferences', [
      ['billing_frequency', 'Billing frequency', ['monthly', 'annual']], ['currency', 'Currency', ['INR', 'USD', 'EUR', 'AED', 'GBP']],
      ['payment_terms_days', 'Payment terms (days)', null, 'number'], ['credit_limit', 'Credit limit (0 = none)', null, 'number'],
      ['account_manager', 'Account manager'], ['segment', 'Segment'],
    ]],
  ];

  function showClientForm(client) {
    const wrap = document.getElementById('client-form-wrap');
    const c = client || { status: 'active', customer_type: 'business', gst_treatment: 'registered', billing_frequency: 'monthly', currency: 'INR', country: 'India', credit_limit: 0 };
    const field = ([k, label, opts, type]) => `<label>${label}${opts
      ? `<select data-k="${k}">${opts.map(o => `<option ${String(c[k] ?? opts[0]) === o ? 'selected' : ''}>${o}</option>`).join('')}</select>`
      : `<input data-k="${k}" type="${type || 'text'}" value="${esc(c[k] ?? '')}" ${client && k === 'enterprise_id' ? 'disabled' : ''} />`}</label>`;
    wrap.innerHTML = `
      <div class="card">
        <h3>${client ? 'Edit' : 'New'} customer</h3>
        ${FORM.map(([title, fields]) => `<div class="form-section"><h4>${title}</h4><div class="form-grid">${fields.map(field).join('')}</div></div>`).join('')}
        <div class="form-section"><h4>Notes</h4><label><textarea data-k="notes" rows="3">${esc(c.notes || '')}</textarea></label></div>
        <div class="actions-row"><button id="save-client">Save customer</button><button class="ghost" id="cancel-client">Cancel</button></div>
      </div>`;
    wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    wrap.querySelector('#cancel-client').addEventListener('click', () => wrap.innerHTML = '');
    wrap.querySelector('#save-client').addEventListener('click', async () => {
      const data = {};
      wrap.querySelectorAll('[data-k]').forEach(el => {
        let v = el.value.trim();
        if (el.type === 'number') v = v === '' ? (el.dataset.k === 'credit_limit' ? 0 : null) : Number(v);
        data[el.dataset.k] = v;
      });
      if (client) data.enterprise_id = client.enterprise_id;
      if (!data.enterprise_id || !data.name) return toast('Enterprise ID and display name are required', true);
      try {
        if (client) await Apx.updateClient(client.id, data);
        else await Apx.createClient(data);
        toast('Customer saved');
        navigate('clients');
      } catch (e) { toast(e.message, true); }
    });
  }

  document.getElementById('add-client')?.addEventListener('click', () => showClientForm(null));
  document.getElementById('bulk-file')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const res = await Apx.bulkImportClients(file, document.getElementById('bulk-update').checked);
      toast(`Imported ${res.created} new, updated ${res.updated}${res.skipped.length ? `, ${res.skipped.length} skipped` : ''}`, false);
      if (res.skipped.length) document.getElementById('client-form-wrap').innerHTML = `<div class="alert-bar warn" style="display:block"><b>Skipped rows</b><br>${res.skipped.slice(0, 12).map(esc).join('<br>')}${res.skipped.length > 12 ? `<br>…and ${res.skipped.length - 12} more` : ''}</div>`;
      else navigate('clients');
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
          <p class="muted">Extraction does not change billing terms. Finance can import this result as draft terms from <a href="#commercials">Commercials → From agreements</a>.</p>
        </div>`;
      btn.textContent = 'Re-extract'; btn.disabled = false;
      btn.closest('tr').querySelector('.badge').outerHTML = '<span class="badge locked">extracted</span>';
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
  const [commercials, clients, agreements] = await Promise.all([Apx.listCommercials(), Apx.listClients(), Apx.listAgreements()]);
  const canEdit = isFinance();
  const tab = canEdit ? (state.cmTab || 'manual') : null;
  const cname = (id) => clients.find(x => x.id === id)?.name || `#${id}`;
  const srcIcon = { manual: 'fa-keyboard', agreement: 'fa-file-contract', excel: 'fa-file-excel' };

  content.innerHTML = `
    ${canEdit ? '' : `<div class="alert-bar warn"><i class="fa-solid fa-lock"></i> <span>Billing terms can only be amended by the <b>Finance</b> role. You can view them here.</span></div>`}
    ${canEdit ? `<div class="tabs">
      <button class="tab ${tab === 'manual' ? 'active' : ''}" data-tab="manual"><i class="fa-solid fa-keyboard"></i> Manual entry</button>
      <button class="tab ${tab === 'agreements' ? 'active' : ''}" data-tab="agreements"><i class="fa-solid fa-file-contract"></i> From agreements</button>
      <button class="tab ${tab === 'excel' ? 'active' : ''}" data-tab="excel"><i class="fa-solid fa-file-excel"></i> Bulk Excel</button>
    </div><div id="cm-panel"></div>` : ''}
    <div class="card">
      <h3>All billing terms</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Client</th><th>Frequency</th><th>Rules</th><th>Status</th><th>Version</th><th>Source</th><th>Last amended</th></tr></thead>
        <tbody>${commercials.map(c => `<tr><td>${esc(cname(c.client_id))}</td><td>${esc(c.billing_frequency)}</td><td>${(c.rules || []).length}</td><td><span class="badge ${c.status}">${c.status}</span></td><td>v${c.version_no}</td>
          <td><i class="fa-solid ${srcIcon[c.source] || 'fa-keyboard'} muted"></i> ${esc(c.source)}${c.source_ref ? ` <span class="muted">· ${esc(c.source_ref)}</span>` : ''}</td>
          <td class="muted">${c.amended_by ? `${esc(c.amended_by)} · ${new Date(c.amended_at).toLocaleDateString()}` : '—'}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No billing terms yet</td></tr>'}</tbody>
      </table></div>
    </div>`;

  if (!canEdit) return;
  content.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => { state.cmTab = b.dataset.tab; navigate('commercials'); }));
  const panel = document.getElementById('cm-panel');
  if (tab === 'manual') renderManualTab(panel, commercials, clients);
  else if (tab === 'agreements') renderAgreementTermsTab(panel, agreements, commercials, cname);
  else renderExcelTermsTab(panel);
}

function renderAgreementTermsTab(panel, agreements, commercials, cname) {
  panel.innerHTML = `
    <div class="card">
      <h3>Import terms from reviewed agreements</h3>
      <p class="muted">Extraction never changes billing terms by itself. Review the extracted result, then import it here as a draft — you can still edit and must lock it before billing.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Client</th><th>Agreement</th><th>Extraction</th><th>Current terms</th><th></th></tr></thead>
        <tbody>${agreements.map(a => {
          const cm = commercials.find(c => c.client_id === a.client_id);
          const locked = cm && cm.status === 'locked';
          return `<tr data-id="${a.id}"><td>${esc(cname(a.client_id))}</td><td>${esc(a.file_name)}</td>
            <td>${a.has_extraction ? '<span class="badge locked">extracted</span>' : '<span class="badge draft">not extracted</span>'}</td>
            <td>${cm ? `<span class="badge ${cm.status}">${cm.status}</span>` : '<span class="badge none">none</span>'}</td>
            <td>${a.has_extraction ? `<button class="import-terms" ${locked ? 'disabled title="Unlock the terms first"' : ''}><i class="fa-solid fa-file-import"></i> Import as draft</button>` : '<span class="muted">Run extraction on the Agreements page</span>'}</td></tr>`;
        }).join('') || '<tr><td colspan="5" class="muted">No agreements uploaded yet — upload one on the Agreements page.</td></tr>'}</tbody>
      </table></div>
    </div>`;
  panel.querySelectorAll('.import-terms').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    try {
      const r = await Apx.importAgreementTerms(id);
      toast(`Imported ${r.rules.length} rule(s) as a draft${r.overall_confidence != null ? ` (confidence ${r.overall_confidence}%)` : ''} — review and lock`);
      state.cmTab = 'manual';
      navigate('commercials');
    } catch (err) { toast(err.message, true); }
  }));
}

function renderExcelTermsTab(panel) {
  panel.innerHTML = `
    <div class="card">
      <h3>Bulk upload terms from Excel</h3>
      <p class="muted">One row per rule — a client with several rules has several rows. Each client is saved as a draft only if all of its rows are valid.</p>
      <div class="actions-row">
        <a class="btn ghost" href="${Apx.termsExcelTemplateUrl()}"><i class="fa-solid fa-download"></i> Download template (.xlsx)</a>
        <input type="file" id="xl-file" accept=".xlsx,.xlsm,.csv" />
        <button id="xl-check"><i class="fa-solid fa-magnifying-glass-chart"></i> Validate file</button>
        <button class="secondary" id="xl-save" disabled><i class="fa-solid fa-floppy-disk"></i> Save valid clients as drafts</button>
      </div>
      <div id="xl-result"></div>
    </div>`;
  const fileEl = panel.querySelector('#xl-file'), saveBtn = panel.querySelector('#xl-save');
  const draw = (res) => {
    panel.querySelector('#xl-result').innerHTML = `
      <p><b>${res.results.filter(r => r.status !== 'error').length}</b> client(s) ${res.dry_run ? 'ready to save' : 'saved'} · <b>${res.failed}</b> with errors</p>
      <div class="table-wrap"><table>
        <thead><tr><th>EnterpriseId</th><th>Rules</th><th>Status</th><th>Issues</th></tr></thead>
        <tbody>${res.results.map(r => `<tr><td>${esc(r.enterprise_id)}</td><td>${r.rules}</td><td><span class="badge ${r.status}">${r.status}</span></td>
          <td style="white-space:normal">${r.errors.map(e => `<div class="muted">${esc(e)}</div>`).join('') || '—'}</td></tr>`).join('')}</tbody>
      </table></div>`;
  };
  fileEl.addEventListener('change', () => { saveBtn.disabled = true; panel.querySelector('#xl-result').innerHTML = ''; });
  panel.querySelector('#xl-check').addEventListener('click', async () => {
    if (!fileEl.files[0]) return toast('Choose a file first', true);
    try {
      const res = await Apx.bulkExcelTerms(fileEl.files[0], true);
      draw(res);
      saveBtn.disabled = res.results.every(r => r.status === 'error');
    } catch (e) { toast(e.message, true); }
  });
  saveBtn.addEventListener('click', async () => {
    try {
      const res = await Apx.bulkExcelTerms(fileEl.files[0], false);
      draw(res);
      saveBtn.disabled = true;
      toast(`${res.saved} client(s) saved as drafts — review and lock them`);
    } catch (e) { toast(e.message, true); }
  });
}

function renderManualTab(content, commercials, clients) {
  content.innerHTML = `
    <div class="actions-row">
      <select id="cm-client">${clients.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.enterprise_id)})</option>`).join('')}</select>
      <button id="cm-load" class="ghost">Load Commercial</button>
    </div>
    <div id="cm-editor"></div>
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

const DS_LABEL = { loading: 'Loading', upi: 'UPI transactions', users: 'Users' };
function renderDatasourceTest(results) {
  return `<div class="ds-test">${Object.entries(results).map(([name, r]) => r.ok
    ? `<div class="ds-row ok"><b>${esc(DS_LABEL[name] || name)}</b><span class="badge locked">${r.rows} row(s) · ${r.seconds}s</span>
        <div class="muted mono">${r.sample.length ? esc(r.columns.join(' | ')) + '<br>' + r.sample.map(x => esc(Object.values(x).join(' | '))).join('<br>') : 'No rows for this period'}</div></div>`
    : `<div class="ds-row bad"><b>${esc(DS_LABEL[name] || name)}</b><span class="badge error">failed</span><div class="muted mono">${esc(r.error)}</div></div>`).join('')}</div>`;
}

async function renderBilling(content) {
  const [runs, ds] = await Promise.all([Apx.listRuns(), Apx.datasource()]);
  const today = new Date();
  const defaultPeriod = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;

  content.innerHTML = `
    <div class="card">
      <div class="flex-between">
        <h3>Run monthly billing</h3>
        ${ds.configured
          ? `<span class="badge locked" title="${esc(ds.dialect)} · ${esc(ds.host)}">Database connected</span>`
          : '<span class="badge draft">Database not configured</span>'}
      </div>
      <p class="muted">Usage — loading, UPI transactions and users — is pulled straight from the platform database for the period you pick. Each customer is billed on its locked terms.</p>
      ${ds.configured ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-plug"></i> Set <code>USAGE_DATABASE_URL</code> on the server to enable the live database feed. Until then, use the CSV fallback below.</div>'}
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.6rem;max-width:640px;">
        <label>Period<input type="month" id="b-period" value="${defaultPeriod}" /></label>
        <label>TDS Rate %<input type="number" id="b-tds" value="0" step="0.1" /></label>
      </div>
      <div class="actions-row">
        <button id="b-run-db" ${ds.configured ? '' : 'disabled'}><i class="fa-solid fa-database"></i> Calculate from database</button>
        <button class="ghost" id="b-test" ${ds.configured ? '' : 'disabled'}><i class="fa-solid fa-vial"></i> Preview usage</button>
      </div>
      <div id="b-test-result"></div>
      <details class="fallback">
        <summary>Fallback: upload CSV exports instead</summary>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.6rem;max-width:640px;margin-top:.8rem">
          <label><span>Loading CSV <a href="${Apx.templateUrl('loading')}">template</a></span><input type="file" id="b-loading" accept=".csv" /></label>
          <label><span>UPI CSV <a href="${Apx.templateUrl('upi')}">template</a></span><input type="file" id="b-upi" accept=".csv" /></label>
          <label><span>Users CSV <a href="${Apx.templateUrl('users')}">template</a></span><input type="file" id="b-users" accept=".csv" /></label>
        </div>
        <div class="actions-row" style="margin-top:.8rem"><button class="ghost" id="b-run">Calculate from CSVs</button></div>
      </details>
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

  const showRunResult = (res) => {
    document.getElementById('b-result').innerHTML = `
      <div class="card">
        <h3>Run #${res.run_id} — ${res.invoices.length} invoice(s) generated <span class="badge none">${esc(res.usage_source)}</span></h3>
        ${res.unknown_enterprise_ids.length ? `<div class="alert-bar warn">Usage found for EnterpriseIds that are not registered customers: ${res.unknown_enterprise_ids.join(', ')}</div>` : ''}
        ${res.skipped.length ? `<h4>Skipped</h4><div class="table-wrap"><table><thead><tr><th>EnterpriseId</th><th>Reason</th></tr></thead><tbody>${res.skipped.map(s => `<tr><td>${esc(s.enterprise_id)}</td><td>${esc(s.reason)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      </div>`;
    viewRun(res.run_id);
  };

  document.getElementById('b-run-db').addEventListener('click', async (e) => {
    const btn = e.currentTarget; btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Reading usage…';
    const fd = new FormData();
    fd.append('period', document.getElementById('b-period').value);
    fd.append('tds_rate', document.getElementById('b-tds').value || '0');
    fd.append('confirm_annual', '');
    try { showRunResult(await Apx.calculateBillingDb(fd)); toast('Billing run created from database usage'); }
    catch (err) { toast(err.message, true); }
    btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-database"></i> Calculate from database';
  });

  document.getElementById('b-test').addEventListener('click', async () => {
    const box = document.getElementById('b-test-result');
    box.innerHTML = '<p class="muted"><i class="fa-solid fa-spinner fa-spin"></i> Querying…</p>';
    try { box.innerHTML = renderDatasourceTest((await Apx.testDatasource(document.getElementById('b-period').value)).results); }
    catch (err) { box.innerHTML = ''; toast(err.message, true); }
  });

  document.getElementById('b-run').addEventListener('click', async () => {
    const loading = document.getElementById('b-loading').files[0];
    const upi = document.getElementById('b-upi').files[0];
    const users = document.getElementById('b-users').files[0];
    if (!loading || !upi || !users) return toast('All three CSVs are required', true);
    const fd = new FormData();
    fd.append('period', document.getElementById('b-period').value);
    fd.append('tds_rate', document.getElementById('b-tds').value || '0');
    fd.append('confirm_annual', '');
    fd.append('loading_file', loading);
    fd.append('upi_file', upi);
    fd.append('users_file', users);
    try { showRunResult(await Apx.calculateBilling(fd)); } catch (e) { toast(e.message, true); }
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
      <thead><tr><th>Invoice No</th><th>Client</th><th>Period</th><th class="right">Total</th><th class="right">Balance Due</th><th>Due</th><th>Status</th><th></th></tr></thead>
      <tbody>${invoices.map(i => `
        <tr data-id="${i.id}">
          <td>${esc(i.invoice_no)}</td><td>${esc(i.client_name)}</td><td>${esc(i.period)}</td>
          <td class="right amount">${money(i.total)}</td>
          <td class="right amount">${money(i.balance_due)}</td>
          <td>${esc(i.due_date || '—')}</td>
          <td><span class="badge ${i.status}">${esc(i.status)}</span>${i.disputed ? ' <span class="badge draft">disputed</span>' : ''}</td>
          <td>
            ${i.status === 'draft' ? '<button class="ghost inv-issue">Issue</button>' : ''}
            ${['issued', 'sent'].includes(i.status) ? '<button class="ghost inv-send">Send</button>' : ''}
            <a class="btn ghost" href="${Apx.invoicePdfUrl(i.id)}" target="_blank">PDF</a>
            ${!i.disputed && i.status !== 'paid' ? '<button class="ghost inv-dispute">Dispute</button>' : ''}
            ${i.disputed ? '<button class="ghost inv-resolve">Resolve</button>' : ''}
          </td>
        </tr>`).join('') || '<tr><td colspan="8" class="muted">No invoices yet</td></tr>'}</tbody>
    </table></div></div>
  `;

  content.querySelectorAll('.inv-issue').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    try { await Apx.issueInvoice(id); toast('Invoice issued'); navigate('invoices'); }
    catch (err) { toast(err.message, true); }
  }));
  content.querySelectorAll('.inv-send').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    try { const r = await Apx.sendInvoice(id); toast(r.emailed ? 'Invoice emailed to client' : 'Marked sent (no SMTP configured — download the PDF to send manually)'); navigate('invoices'); }
    catch (err) { toast(err.message, true); }
  }));
  content.querySelectorAll('.inv-dispute').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    const reason = prompt('Reason for dispute?', '');
    if (reason === null) return;
    try { await Apx.disputeInvoice(id, true, reason); toast('Marked disputed — aging clock paused'); navigate('invoices'); }
    catch (err) { toast(err.message, true); }
  }));
  content.querySelectorAll('.inv-resolve').forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    try { await Apx.disputeInvoice(id, false, ''); toast('Dispute resolved'); navigate('invoices'); }
    catch (err) { toast(err.message, true); }
  }));
}

// ---------------------------------------------------------------------------
// Receivables — aging + reminders
// ---------------------------------------------------------------------------

const BUCKET_LABELS = { current: 'Current', '0_30': '1–30 days', '31_60': '31–60 days', '61_90': '61–90 days', '90_plus': '90+ days' };

async function renderReceivables(content) {
  const aging = await Apx.agingReport();
  content.innerHTML = `
    <div class="stat-grid">
      ${Object.entries(aging.totals).map(([bucket, total]) => `<div class="stat-card"><div class="value">${money(total)}</div><div class="label">${BUCKET_LABELS[bucket]}</div></div>`).join('')}
    </div>
    <div class="card flex-between">
      <div><h3 style="margin:0">Total Outstanding: ${money(aging.total_outstanding)}</h3></div>
      <button id="run-reminders">Send Overdue Reminders</button>
    </div>
    ${Object.entries(aging.buckets).filter(([, rows]) => rows.length).map(([bucket, rows]) => `
      <div class="card">
        <h3>${BUCKET_LABELS[bucket]}</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>Invoice</th><th>Client</th><th>Due</th><th class="right">Balance</th><th>Days Overdue</th></tr></thead>
          <tbody>${rows.map(r => `<tr><td>${esc(r.invoice_no)}</td><td>${esc(r.client_name)}</td><td>${esc(r.due_date || '—')}</td><td class="right amount">${money(r.balance_due)}</td><td>${r.days_overdue ?? '—'}${r.disputed ? ' <span class="badge draft">disputed</span>' : ''}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>`).join('') || '<div class="card"><p class="muted">Nothing outstanding.</p></div>'}
    <div class="card">
      <h3>Reminder Log</h3>
      <div id="reminders-log" class="table-wrap"></div>
    </div>
  `;
  document.getElementById('run-reminders').addEventListener('click', async () => {
    try {
      const r = await Apx.runReminders();
      toast(`${r.sent.length} emailed, ${r.logged_only.length} logged (no SMTP / no contact email)`);
      navigate('receivables');
    } catch (e) { toast(e.message, true); }
  });
  const log = await Apx.remindersLog();
  document.getElementById('reminders-log').innerHTML = `
    <table><thead><tr><th>Invoice</th><th>Days Overdue</th><th>Channel</th><th>Sent</th></tr></thead>
    <tbody>${log.map(r => `<tr><td>${esc(r.invoice_no)}</td><td>${r.days_overdue}</td><td>${esc(r.channel)}</td><td>${new Date(r.sent_at).toLocaleString()}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No reminders sent yet</td></tr>'}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Reconciliation
// ---------------------------------------------------------------------------

async function renderReconciliation(content) {
  const queue = await Apx.reconciliationQueue();
  content.innerHTML = `
    <div class="card">
      <h3>Upload Bank Statement</h3>
      <p class="muted">Any CSV export from your bank — date / narration / credit columns are detected automatically.</p>
      <div class="actions-row">
        <input type="file" id="stmt-file" accept=".csv" />
        <button id="stmt-upload">Upload &amp; Reconcile</button>
      </div>
    </div>
    <div class="card">
      <h3>Or Upload a Manual Collections Sheet</h3>
      <p class="muted">Columns: enterprise_id, amount, date, mode, reference</p>
      <div class="actions-row">
        <input type="file" id="manual-file" accept=".csv" />
        <button class="secondary" id="manual-upload">Upload</button>
      </div>
    </div>
    <div id="recon-result"></div>
    <div class="card">
      <h3>Review Queue <span class="muted" style="font-size:0.8rem">(${queue.length} pending)</span></h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Date</th><th class="right">Amount</th><th>Narration / Client</th><th>Confidence</th><th>Match to</th><th></th></tr></thead>
        <tbody id="queue-rows">${queue.map(p => `
          <tr data-id="${p.id}">
            <td>${esc(p.date || '—')}</td>
            <td class="right amount">${money(p.amount)}</td>
            <td>${esc(p.client_name || p.narration || '—')}</td>
            <td><span class="badge draft">${esc(p.match_confidence || 'none')}</span></td>
            <td>
              <select class="q-invoice">
                <option value="">— choose invoice —</option>
                ${p.candidate_invoices.map(c => `<option value="${c.id}">${esc(c.invoice_no)} (${money(c.balance_due)})</option>`).join('')}
              </select>
            </td>
            <td><button class="ghost q-confirm">Confirm</button><button class="ghost q-dismiss">Dismiss</button></td>
          </tr>`).join('') || '<tr><td colspan="6" class="muted">Nothing pending review</td></tr>'}</tbody>
      </table></div>
    </div>
  `;

  document.getElementById('stmt-upload').addEventListener('click', async () => {
    const file = document.getElementById('stmt-file').files[0];
    if (!file) return toast('Choose a statement CSV first', true);
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await Apx.uploadStatement(fd);
      document.getElementById('recon-result').innerHTML = `<div class="card"><h3>Reconciled</h3><p>${r.auto_matched} auto-matched · ${r.queued} queued for review · ${r.unmatched} unidentified</p></div>`;
      navigate('reconciliation');
    } catch (e) { toast(e.message, true); }
  });

  document.getElementById('manual-upload').addEventListener('click', async () => {
    const file = document.getElementById('manual-file').files[0];
    if (!file) return toast('Choose a collections CSV first', true);
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await Apx.manualCollectionsUpload(fd);
      toast(`${r.auto_matched} matched, ${r.queued} queued, ${r.unmatched} unknown EnterpriseId`);
      navigate('reconciliation');
    } catch (e) { toast(e.message, true); }
  });

  content.querySelectorAll('.q-confirm').forEach(btn => btn.addEventListener('click', async (e) => {
    const row = e.target.closest('tr');
    const invoiceId = row.querySelector('.q-invoice').value;
    if (!invoiceId) return toast('Pick an invoice to match', true);
    try {
      const r = await Apx.confirmMatch(row.dataset.id, Number(invoiceId));
      toast(`Allocated ${money(r.allocated)}${r.remainder_queued ? ' — remainder re-queued' : ''}`);
      navigate('reconciliation');
    } catch (e2) { toast(e2.message, true); }
  }));
  content.querySelectorAll('.q-dismiss').forEach(btn => btn.addEventListener('click', async (e) => {
    const row = e.target.closest('tr');
    if (!confirm('Dismiss this receipt as unrelated to any invoice?')) return;
    await Apx.dismissPayment(row.dataset.id);
    toast('Dismissed');
    navigate('reconciliation');
  }));
}

// ---------------------------------------------------------------------------
// CRM Sync
// ---------------------------------------------------------------------------

async function renderCrm(content) {
  const runs = await Apx.crmSyncRuns();
  content.innerHTML = `
    <div class="card">
      <h3>Sync Clients from CRM</h3>
      <p class="muted">The CRM stays the source of truth for name / GSTIN / state / contact — billing-specific fields here (frequency, commercials, invoices) are never overwritten by a sync. Export your CRM's accounts to CSV (<a href="${Apx.crmTemplateUrl()}">expected columns</a>) and upload it below.</p>
      <div class="actions-row">
        <input type="file" id="crm-file" accept=".csv" />
        <button id="crm-sync-btn">Sync Now</button>
      </div>
    </div>
    <div class="card">
      <h3>Sync History</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>When</th><th>Source</th><th>Created</th><th>Updated</th><th>By</th></tr></thead>
        <tbody>${runs.map(r => `<tr><td>${new Date(r.run_at).toLocaleString()}</td><td>${esc(r.crm_source)}</td><td>${r.created}</td><td>${r.updated}</td><td>${esc(r.run_by)}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No syncs yet</td></tr>'}</tbody>
      </table></div>
    </div>
  `;
  document.getElementById('crm-sync-btn').addEventListener('click', async () => {
    const file = document.getElementById('crm-file').files[0];
    if (!file) return toast('Choose a CRM export CSV first', true);
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await Apx.crmSync(fd, 'generic_csv');
      toast(`${r.created} created, ${r.updated} updated`);
      navigate('crm');
    } catch (e) { toast(e.message, true); }
  });
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
  const [settings, providers, health, ds] = await Promise.all([Apx.getSettings(), Apx.providers(), Apx.health(), Apx.datasource()]);
  const isAdmin = state.user.role === 'admin';
  content.innerHTML = `
    <div class="card">
      <h3>Organisation Settings</h3>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.6rem;max-width:640px;">
        ${Object.entries(settings).map(([k, v]) => `<label>${esc(k.replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase()).replace(/\bgst\b/gi, 'GST'))}<input class="s-field" data-key="${k}" value="${esc(v)}" /></label>`).join('')}
      </div>
      <div class="actions-row"><button id="save-settings">Save Settings</button></div>
    </div>
    <div class="card">
      <div class="flex-between"><h3>Usage data source</h3>
        ${ds.configured ? `<span class="badge locked">${esc(ds.dialect)} · ${esc(ds.host)}</span>` : '<span class="badge draft">not configured</span>'}</div>
      <p class="muted">Monthly billing reads usage from this database. The connection string lives in the server environment (<code>USAGE_DATABASE_URL</code>) and is never shown here — use a read-only database user. Queries must be a single <code>SELECT</code> and can use <code>:period_start</code> / <code>:period_end</code>.</p>
      ${['loading', 'upi', 'users'].map(k => `<label>${{ loading: 'Loading — returns enterprise_id, amount', upi: 'UPI — returns enterprise_id, txn_count, txn_amount', users: 'Users — returns enterprise_id, admin_users, spender_users, total_users' }[k]}
        <textarea class="ds-q mono" data-k="${k}" rows="6" ${isAdmin ? '' : 'readonly'}>${esc(ds.queries[k])}</textarea></label>`).join('')}
      <div class="actions-row">
        ${isAdmin ? '<button id="ds-save">Save queries</button><button class="ghost" id="ds-reset">Reset to defaults</button>' : '<span class="muted">Only an admin can edit these queries.</span>'}
        <input type="month" id="ds-period" value="${new Date().toISOString().slice(0, 7)}" />
        <button class="ghost" id="ds-test" ${ds.configured ? '' : 'disabled'} title="Tests the saved queries"><i class="fa-solid fa-vial"></i> Test saved queries</button>
      </div>
      <div id="ds-test-result"></div>
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
  const readQueries = () => Object.fromEntries([...content.querySelectorAll('.ds-q')].map(el => [el.dataset.k, el.value]));
  document.getElementById('ds-save')?.addEventListener('click', async () => {
    try { await Apx.saveDatasource(readQueries()); toast('Queries saved'); } catch (e) { toast(e.message, true); }
  });
  document.getElementById('ds-reset')?.addEventListener('click', () => {
    content.querySelectorAll('.ds-q').forEach(el => { el.value = ds.defaults[el.dataset.k]; });
    toast('Defaults restored — click Save to keep them');
  });
  document.getElementById('ds-test').addEventListener('click', async () => {
    const box = document.getElementById('ds-test-result');
    box.innerHTML = '<p class="muted"><i class="fa-solid fa-spinner fa-spin"></i> Querying…</p>';
    try {
      box.innerHTML = renderDatasourceTest((await Apx.testDatasource(document.getElementById('ds-period').value)).results);
    } catch (e) { box.innerHTML = ''; toast(e.message, true); }
  });
}

const RENDERERS = {
  dashboard: renderDashboard,
  clients: renderClients,
  agreements: renderAgreements,
  commercials: renderCommercials,
  billing: renderBilling,
  invoices: renderInvoices,
  receivables: renderReceivables,
  reconciliation: renderReconciliation,
  crm: renderCrm,
  reports: renderReports,
  metrics: renderMetrics,
  users: renderUsers,
  audit: renderAudit,
  settings: renderSettings,
};

boot();
