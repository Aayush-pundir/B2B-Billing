// ---------------------------------------------------------------------------
// Phase 3 modules: Items, Sales documents, Purchases, Accounting, Financial reports
// Loaded before app.js; uses its globals (state, esc, money, toast, navigate, Apx) at call time.
// ---------------------------------------------------------------------------

const todayStr = () => new Date().toISOString().slice(0, 10);
const fyStart = () => { const d = new Date(); const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; return `${y}-04-01`; };
const canSales = () => ['accounts', 'finance'].includes(state.user.role);
const canBooks = () => state.user.role === 'finance';
const optList = (rows, val, label, sel) => rows.map(r => `<option value="${esc(r[val])}" ${String(r[val]) === String(sel) ? 'selected' : ''}>${esc(typeof label === 'function' ? label(r) : r[label])}</option>`).join('');
const tabBar = (tabs, active) => `<div class="tabs">${tabs.map(([id, label, icon]) => `<button class="tab ${id === active ? 'active' : ''}" data-tab="${id}"><i class="fa-solid ${icon}"></i> ${label}</button>`).join('')}</div>`;
const tbl = (head, rows, empty) => `<div class="table-wrap"><table><thead><tr>${head.map(h => `<th${h.startsWith('>') ? ' class="right"' : ''}>${esc(h.replace(/^>/, ''))}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${head.length}" class="muted">${empty}</td></tr>`}</tbody></table></div>`;
const bindTabs = (content, key, page) => content.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => { state[key] = b.dataset.tab; navigate(page); }));
const csvDownload = (name, rows) => {
  const csv = rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = name; a.click();
};

// ---------- reusable line editor for sales documents ----------
function salesLines(host, items, onChange) {
  const active = items.filter(i => i.active);
  const row = () => `<div class="line-row">
    <select class="l-item"><option value="">Custom line…</option>${active.map(i => `<option value="${i.id}">${esc(i.name)}</option>`).join('')}</select>
    <input class="l-desc" placeholder="Description" />
    <input class="l-qty" type="number" min="0" step="any" value="1" title="Quantity" />
    <input class="l-rate" type="number" min="0" step="any" placeholder="Rate ₹" />
    <input class="l-gst" type="number" min="0" max="40" step="any" placeholder="GST %" />
    <i class="fa-solid fa-trash l-rm" title="Remove line"></i></div>`;
  host.innerHTML = '<div class="lines"></div><button class="ghost add-line" type="button"><i class="fa-solid fa-plus"></i> Add line</button>';
  const list = host.querySelector('.lines');
  const add = () => {
    list.insertAdjacentHTML('beforeend', row());
    const r = list.lastElementChild;
    r.querySelector('.l-item').addEventListener('change', (e) => {
      const it = items.find(i => String(i.id) === e.target.value);
      if (it) { r.querySelector('.l-rate').value = it.rate; r.querySelector('.l-gst').value = it.gst_rate; r.querySelector('.l-desc').placeholder = it.name; }
      onChange();
    });
    r.querySelector('.l-rm').addEventListener('click', () => { if (list.children.length > 1) { r.remove(); onChange(); } });
    r.addEventListener('input', onChange);
  };
  add();
  host.querySelector('.add-line').addEventListener('click', add);
  return () => [...list.children].map(r => {
    const v = (c) => r.querySelector(c).value;
    return { item_id: v('.l-item') ? Number(v('.l-item')) : null, description: v('.l-desc'), qty: Number(v('.l-qty') || 1),
             rate: v('.l-rate') === '' ? null : Number(v('.l-rate')), gst_rate: v('.l-gst') === '' ? null : Number(v('.l-gst')) };
  });
}

// One composer for invoice | debit_note | quote | recurring
function docComposer(panel, kind, { clients, items }) {
  const titles = { invoice: 'New invoice', debit_note: 'New debit note', quote: 'New quote', recurring: 'New recurring invoice' };
  const active = clients.filter(c => (c.status || 'active') === 'active');
  panel.innerHTML = `
    <div class="card"><h3>${titles[kind]}</h3>
      <div class="form-grid">
        <label>Customer<select id="d-client">${optList(active, 'id', c => `${c.name} (${c.enterprise_id})`)}</select></label>
        ${kind === 'recurring' ? `<label>Starts on<input type="date" id="d-start" value="${todayStr()}" /></label>
          <label>Repeats<select id="d-freq"><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="half_yearly">Half-yearly</option><option value="annual">Annual</option></select></label>
          <label>Ends on (optional)<input type="date" id="d-end" /></label>
          <label>Name<input id="d-name" placeholder="e.g. Hosting retainer" /></label>`
        : `<label>Date<input type="date" id="d-date" value="${todayStr()}" /></label>`}
        ${kind === 'quote' ? '<label>Valid for (days)<input type="number" id="d-valid" value="30" min="1" /></label>' : ''}
        ${kind === 'invoice' ? '<label>Payment terms (days)<input type="number" id="d-terms" placeholder="customer default" min="0" /></label><label>PO number<input id="d-po" /></label>' : ''}
        ${kind === 'debit_note' ? '<label>Against invoice (optional)<select id="d-parent"><option value="">—</option></select></label>' : ''}
      </div>
      <h4 style="margin-top:1rem">Lines</h4><div id="d-lines"></div>
      <div class="doc-total" id="d-total"><span class="muted">Add a rate to see totals</span></div>
      ${kind === 'quote' ? '<label>Notes<textarea id="d-notes" rows="2"></textarea></label>' : ''}
      <div class="actions-row">
        ${['invoice', 'debit_note', 'recurring'].includes(kind) ? `<label class="inline-check"><input type="checkbox" id="d-issue" checked /> issue &amp; email now</label>` : ''}
        <button id="d-save"><i class="fa-solid fa-check"></i> Create ${kind.replace('_', ' ')}</button>
      </div>
    </div>`;
  const clientEl = panel.querySelector('#d-client');
  let timer;
  const refresh = async () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const box = panel.querySelector('#d-total');
      if (!clientEl.value) return;
      try {
        const r = await Apx.previewDoc(Number(clientEl.value), getLines());
        box.innerHTML = `<div><span>Subtotal</span><b>${money(r.subtotal)}</b></div><div><span>${r.gst_type === 'IGST' ? 'IGST' : 'CGST + SGST'}</span><b>${money(r.gst)}</b></div><div class="grand"><span>Total</span><b>${money(r.total)}</b></div>`;
      } catch (e) { box.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
    }, 250);
  };
  const getLines = salesLines(panel.querySelector('#d-lines'), items, refresh);
  clientEl.addEventListener('change', async () => { refresh(); loadParents(); });
  async function loadParents() {
    const sel = panel.querySelector('#d-parent'); if (!sel) return;
    const invs = (await Apx.listInvoices()).filter(i => !['draft', 'cancelled'].includes(i.status));
    const mine = clients.find(c => String(c.id) === clientEl.value);
    sel.innerHTML = '<option value="">—</option>' + invs.filter(i => i.enterprise_id === mine?.enterprise_id).map(i => `<option value="${i.id}">${esc(i.invoice_no)} · ${esc(i.period)}</option>`).join('');
  }
  loadParents();

  panel.querySelector('#d-save').addEventListener('click', async (e) => {
    const btn = e.currentTarget; btn.disabled = true;
    const v = (id) => panel.querySelector(id)?.value;
    const base = { client_id: Number(clientEl.value), lines: getLines() };
    try {
      let r;
      if (kind === 'invoice') r = await Apx.createInvoiceDoc({ ...base, date: v('#d-date'), terms_days: v('#d-terms') ? Number(v('#d-terms')) : null, po_number: v('#d-po') || '', issue: panel.querySelector('#d-issue').checked });
      else if (kind === 'debit_note') r = await Apx.createDebitNote({ ...base, date: v('#d-date'), parent_invoice_id: v('#d-parent') ? Number(v('#d-parent')) : null, issue: panel.querySelector('#d-issue').checked });
      else if (kind === 'quote') r = await Apx.createQuote({ ...base, date: v('#d-date'), valid_days: Number(v('#d-valid') || 30), notes: v('#d-notes') || '' });
      else r = await Apx.createRecurring({ ...base, name: v('#d-name'), frequency: v('#d-freq'), start_date: v('#d-start'), end_date: v('#d-end') || null, auto_issue: panel.querySelector('#d-issue').checked });
      toast(r.invoice_no ? `${r.invoice_no} created (${money(r.total)})` : r.quote_no ? `${r.quote_no} created` : `Recurring invoice saved — ${money(r.each_invoice_total)} each`);
      state.salesTab = { invoice: 'documents', debit_note: 'documents', quote: 'quotes', recurring: 'recurring' }[kind];
      navigate('sales');
    } catch (err) { toast(err.message, true); btn.disabled = false; }
  });
}

// ===========================================================================
// Items
// ===========================================================================
async function renderItems(content) {
  const [items, accts] = await Promise.all([Apx.listItems(), Apx.listAccounts().catch(() => [])]);
  const income = accts.filter(a => a.type === 'income');
  const can = ['admin', 'accounts', 'finance'].includes(state.user.role);
  content.innerHTML = `
    ${can ? '<div class="actions-row"><button id="it-new"><i class="fa-solid fa-plus"></i> New item</button></div>' : ''}
    <div id="it-form"></div>
    <div class="card">${tbl(['Code', 'Item', 'Type', 'HSN / SAC', '>Rate', 'GST %', 'Revenue account', 'Status', ''],
      items.map(i => `<tr data-id="${i.id}"><td class="mono">${esc(i.code)}</td><td><b>${esc(i.name)}</b><div class="muted" style="font-size:.74rem">per ${esc(i.unit)}</div></td><td>${esc(i.item_type)}</td><td class="mono">${esc(i.hsn_sac || '—')}</td><td class="right amount">${money(i.rate)}</td><td>${i.gst_rate}%</td><td class="mono">${esc(i.revenue_account)}</td><td><span class="badge ${i.active ? 'locked' : 'none'}">${i.active ? 'active' : 'inactive'}</span></td><td>${can ? '<i class="fa-solid fa-pen it-edit" style="cursor:pointer"></i>' : ''}</td></tr>`),
      'No items yet — add the services and products you sell.')}</div>`;
  const form = (it) => {
    const c = it || { code: '', name: '', item_type: 'service', hsn_sac: '', unit: 'nos', rate: 0, gst_rate: 18, revenue_account: '4000', description: '', active: true };
    document.getElementById('it-form').innerHTML = `<div class="card"><h3>${it ? 'Edit' : 'New'} item</h3>
      <div class="form-grid">
        <label>Code *<input data-k="code" value="${esc(c.code)}" ${it ? 'disabled' : ''} /></label><label>Name *<input data-k="name" value="${esc(c.name)}" /></label>
        <label>Type<select data-k="item_type"><option ${c.item_type === 'service' ? 'selected' : ''}>service</option><option ${c.item_type === 'goods' ? 'selected' : ''}>goods</option></select></label>
        <label>HSN / SAC<input data-k="hsn_sac" value="${esc(c.hsn_sac)}" /></label><label>Unit<input data-k="unit" value="${esc(c.unit)}" /></label>
        <label>Default rate ₹<input data-k="rate" type="number" step="any" value="${c.rate}" /></label>
        <label>GST %<select data-k="gst_rate">${[0, 5, 12, 18, 28].map(r => `<option ${Number(c.gst_rate) === r ? 'selected' : ''}>${r}</option>`).join('')}</select></label>
        <label>Revenue account<select data-k="revenue_account">${income.length ? optList(income, 'code', a => `${a.code} · ${a.name}`, c.revenue_account) : `<option>${esc(c.revenue_account)}</option>`}</select></label>
        <label>Active<select data-k="active"><option value="true" ${c.active ? 'selected' : ''}>yes</option><option value="false" ${c.active ? '' : 'selected'}>no</option></select></label>
      </div>
      <div class="actions-row" style="margin-top:1rem"><button id="it-save">Save item</button><button class="ghost" id="it-cancel">Cancel</button></div></div>`;
    document.getElementById('it-cancel').onclick = () => { document.getElementById('it-form').innerHTML = ''; };
    document.getElementById('it-save').onclick = async () => {
      const d = {}; document.querySelectorAll('#it-form [data-k]').forEach(e => { d[e.dataset.k] = e.value; });
      d.rate = Number(d.rate || 0); d.gst_rate = Number(d.gst_rate); d.active = d.active === 'true'; if (it) d.code = it.code;
      try { await Apx.saveItem(it?.id, d); toast('Item saved'); navigate('items'); } catch (e) { toast(e.message, true); }
    };
  };
  document.getElementById('it-new')?.addEventListener('click', () => form(null));
  content.querySelectorAll('.it-edit').forEach(b => b.addEventListener('click', e => form(items.find(i => String(i.id) === e.target.closest('tr').dataset.id))));
}

// ===========================================================================
// Sales
// ===========================================================================
async function renderSales(content) {
  const tab = state.salesTab || 'documents';
  const [clients, items] = await Promise.all([Apx.listClients(), Apx.listItems()]);
  const tabs = [['documents', 'Documents', 'fa-file-lines'], ['invoice', 'New invoice', 'fa-file-circle-plus'], ['debit_note', 'Debit note', 'fa-file-circle-exclamation'],
                ['quotes', 'Quotes', 'fa-file-signature'], ['recurring', 'Recurring', 'fa-repeat'], ['receipts', 'Receipts & advances', 'fa-hand-holding-dollar']];
  content.innerHTML = `${canSales() ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-lock"></i><span>Only Accounts and Finance can create sales documents.</span></div>'}${tabBar(tabs, tab)}<div id="sl-panel"></div>`;
  bindTabs(content, 'salesTab', 'sales');
  const panel = document.getElementById('sl-panel');
  if (['invoice', 'debit_note'].includes(tab)) return docComposer(panel, tab, { clients, items });

  if (tab === 'documents') {
    const docs = await Apx.listDocs();
    panel.innerHTML = `<div class="card"><h3>Ad-hoc, recurring &amp; debit-note documents</h3><p class="muted">Usage-based invoices from monthly billing are on the Invoices page.</p>${tbl(['Number', 'Type', 'Source', 'Customer', 'Period', '>Total', '>Balance', 'Status', ''],
      docs.map(d => `<tr data-id="${d.id}"><td><b>${esc(d.invoice_no)}</b></td><td>${d.doc_type === 'debit_note' ? '<span class="badge draft">debit note</span>' : 'invoice'}</td><td class="muted">${esc(d.source)}</td><td>${esc(d.client)}</td><td>${esc(d.period)}</td><td class="right amount">${money(d.total)}</td><td class="right amount">${money(d.balance_due)}</td><td><span class="badge ${d.status}">${esc(d.status.replace('_', ' '))}</span></td>
        <td><a class="btn ghost" href="${Apx.invoicePdfUrl(d.id)}" target="_blank">PDF</a> ${canSales() && d.status === 'draft' ? '<button class="ghost doc-issue">Issue</button>' : ''} ${canSales() && ['issued', 'sent', 'partially_paid', 'overdue'].includes(d.status) && d.balance_due > 0 ? '<button class="ghost doc-wo">Write off</button>' : ''}</td></tr>`),
      'Nothing here yet — create an invoice or debit note.')}</div>`;
    panel.querySelectorAll('.doc-issue').forEach(b => b.addEventListener('click', async e => { try { await Apx.issueInvoice(e.target.closest('tr').dataset.id); toast('Issued'); navigate('sales'); } catch (er) { toast(er.message, true); } }));
    panel.querySelectorAll('.doc-wo').forEach(b => b.addEventListener('click', async e => {
      const reason = prompt('Write off the remaining balance. Reason?', 'Uncollectible'); if (reason === null) return;
      try { const r = await Apx.writeOff(e.target.closest('tr').dataset.id, reason); toast(`${money(r.written_off)} written off`); navigate('sales'); } catch (er) { toast(er.message, true); }
    }));
  }

  if (tab === 'quotes') {
    const quotes = await Apx.listQuotes();
    panel.innerHTML = `${canSales() ? '<div class="actions-row"><button id="q-new"><i class="fa-solid fa-plus"></i> New quote</button></div>' : ''}<div id="q-form"></div>
      <div class="card">${tbl(['Quote', 'Customer', 'Date', 'Valid until', '>Total', 'Status', ''], quotes.map(q => `<tr data-id="${q.id}"><td><b>${esc(q.quote_no)}</b></td><td>${esc(q.customer)}</td><td>${esc(q.date)}</td><td>${esc(q.valid_until || '—')}</td><td class="right amount">${money(q.total)}</td><td><span class="badge ${q.status === 'accepted' || q.status === 'invoiced' ? 'locked' : q.status === 'declined' || q.status === 'expired' ? 'error' : 'draft'}">${esc(q.status)}</span></td>
        <td><a class="btn ghost" href="${Apx.quotePdfUrl(q.id)}" target="_blank">PDF</a>
          ${canSales() && !['invoiced', 'declined'].includes(q.status) ? `${q.status !== 'sent' && q.status !== 'accepted' ? '<button class="ghost q-st" data-s="sent">Mark sent</button>' : ''}${q.status !== 'accepted' ? '<button class="ghost q-st" data-s="accepted">Accepted</button>' : ''}<button class="ghost q-st" data-s="declined">Declined</button><button class="q-conv">Invoice it</button>` : ''}
          ${q.invoice_id ? '<span class="muted">invoiced</span>' : ''}</td></tr>`), 'No quotes yet.')}</div>`;
    document.getElementById('q-new')?.addEventListener('click', () => docComposer(document.getElementById('q-form'), 'quote', { clients, items }));
    panel.querySelectorAll('.q-st').forEach(b => b.addEventListener('click', async e => { try { await Apx.setQuoteStatus(e.target.closest('tr').dataset.id, b.dataset.s); navigate('sales'); } catch (er) { toast(er.message, true); } }));
    panel.querySelectorAll('.q-conv').forEach(b => b.addEventListener('click', async e => { try { const r = await Apx.convertQuote(e.target.closest('tr').dataset.id); toast(`Created ${r.invoice_no} (draft) — issue it from Documents`); state.salesTab = 'documents'; navigate('sales'); } catch (er) { toast(er.message, true); } }));
  }

  if (tab === 'recurring') {
    const recs = await Apx.listRecurring();
    panel.innerHTML = `${canSales() ? '<div class="actions-row"><button id="r-new"><i class="fa-solid fa-plus"></i> New recurring invoice</button><button class="ghost" id="r-run"><i class="fa-solid fa-play"></i> Generate due invoices now</button></div>' : ''}<div id="r-form"></div>
      <div class="card"><p class="muted">Generated automatically by the scheduler. Missed cycles are caught up when it next runs.</p>${tbl(['Name', 'Customer', 'Repeats', 'Next invoice', 'Ends', 'Generated', 'Status', ''], recs.map(r => `<tr data-id="${r.id}"><td><b>${esc(r.name)}</b></td><td>${esc(r.customer)}</td><td>${esc(r.frequency.replace('_', '-'))}</td><td>${esc(r.next_date)}</td><td>${esc(r.end_date || '—')}</td><td>${r.generated_count}</td><td><span class="badge ${r.status === 'active' ? 'locked' : r.status === 'paused' ? 'draft' : 'none'}">${r.status}</span></td>
        <td>${canSales() && r.status === 'active' ? '<button class="ghost r-st" data-s="paused">Pause</button>' : ''}${canSales() && r.status === 'paused' ? '<button class="ghost r-st" data-s="active">Resume</button>' : ''}${canSales() && r.status !== 'ended' ? '<button class="ghost r-st" data-s="ended">End</button>' : ''}</td></tr>`), 'No recurring invoices yet.')}</div>`;
    document.getElementById('r-new')?.addEventListener('click', () => docComposer(document.getElementById('r-form'), 'recurring', { clients, items }));
    document.getElementById('r-run')?.addEventListener('click', async () => { try { const r = await Apx.runRecurring(); toast(`${r.generated.filter(g => g.invoice_no).length} invoice(s) generated`); navigate('sales'); } catch (e) { toast(e.message, true); } });
    panel.querySelectorAll('.r-st').forEach(b => b.addEventListener('click', async e => { try { await Apx.setRecurringStatus(e.target.closest('tr').dataset.id, b.dataset.s); navigate('sales'); } catch (er) { toast(er.message, true); } }));
  }

  if (tab === 'receipts') {
    const [adv, invoices] = await Promise.all([Apx.listAdvances().catch(() => []), Apx.listInvoices()]);
    const open = invoices.filter(i => ['issued', 'sent', 'partially_paid', 'overdue'].includes(i.status) && i.balance_due > 0);
    panel.innerHTML = `${canSales() ? `<div class="card"><h3>Record a receipt</h3><p class="muted">Money received outside a bank-statement import. Without an invoice it is held as a customer advance and can be applied later.</p>
      <div class="form-grid"><label>Customer<select id="rc-client">${optList(clients, 'id', c => `${c.name} (${c.enterprise_id})`)}</select></label>
        <label>Amount ₹<input id="rc-amt" type="number" step="any" min="0" /></label><label>Date<input id="rc-date" type="date" value="${todayStr()}" /></label>
        <label>Mode<select id="rc-mode"><option>NEFT</option><option>RTGS</option><option>UPI</option><option>Cheque</option><option>Cash</option></select></label>
        <label>Reference / UTR<input id="rc-ref" /></label><label>Apply to invoice<select id="rc-inv"><option value="">Keep as advance</option></select></label></div>
      <div class="actions-row" style="margin-top:1rem"><button id="rc-save"><i class="fa-solid fa-check"></i> Record receipt</button></div></div>` : ''}
      <div class="card"><h3>Unallocated advances</h3>${tbl(['Date', 'Customer', 'Reference', '>Amount', 'Apply to'], adv.map(a => `<tr data-id="${a.id}"><td>${esc(a.date)}</td><td>${esc(a.customer)}</td><td>${esc(a.reference || '—')}</td><td class="right amount">${money(a.amount)}</td>
        <td>${canSales() && a.open_invoices.length ? `<select class="adv-inv">${a.open_invoices.map(i => `<option value="${i.id}">${esc(i.invoice_no)} · ${money(i.balance_due)}</option>`).join('')}</select> <button class="ghost adv-apply">Apply</button>` : '<span class="muted">no open invoices</span>'}</td></tr>`), 'No unallocated advances.')}</div>`;
    const sync = () => { const sel = document.getElementById('rc-inv'); if (!sel) return; const cid = document.getElementById('rc-client').value; const me = clients.find(c => String(c.id) === cid);
      sel.innerHTML = '<option value="">Keep as advance</option>' + open.filter(i => i.enterprise_id === me?.enterprise_id).map(i => `<option value="${i.id}">${esc(i.invoice_no)} · ${money(i.balance_due)} due</option>`).join(''); };
    document.getElementById('rc-client')?.addEventListener('change', sync); sync();
    document.getElementById('rc-save')?.addEventListener('click', async () => {
      try { await Apx.recordReceipt({ client_id: Number(document.getElementById('rc-client').value), amount: Number(document.getElementById('rc-amt').value), date: document.getElementById('rc-date').value, mode: document.getElementById('rc-mode').value, reference: document.getElementById('rc-ref').value, invoice_id: document.getElementById('rc-inv').value ? Number(document.getElementById('rc-inv').value) : null });
        toast('Receipt recorded'); navigate('sales'); } catch (e) { toast(e.message, true); } });
    panel.querySelectorAll('.adv-apply').forEach(b => b.addEventListener('click', async e => { const tr = e.target.closest('tr');
      try { await Apx.confirmMatch(tr.dataset.id, Number(tr.querySelector('.adv-inv').value)); toast('Advance applied'); navigate('sales'); } catch (er) { toast(er.message, true); } }));
  }
}

// ===========================================================================
// Purchases
// ===========================================================================
async function renderPurchases(content) {
  const tab = state.purTab || 'bills';
  const [vendors, bills, accts] = await Promise.all([Apx.listVendors(), Apx.listBills(), Apx.listAccounts()]);
  const spend = accts.filter(a => ['expense', 'asset'].includes(a.type) && a.active);
  const tabs = [['bills', 'Bills', 'fa-file-invoice'], ['newbill', 'New bill', 'fa-plus'], ['vendors', 'Vendors', 'fa-truck-field']];
  content.innerHTML = `${canBooks() ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-lock"></i><span>Only Finance can record bills and vendors. You can view them.</span></div>'}${tabBar(tabs, tab)}<div id="pu-panel"></div>`;
  bindTabs(content, 'purTab', 'purchases');
  const panel = document.getElementById('pu-panel');

  if (tab === 'bills') {
    const owed = bills.filter(b => ['open', 'partially_paid'].includes(b.status)).reduce((s, b) => s + b.balance_due, 0);
    panel.innerHTML = `<div class="stat-grid"><div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-file-invoice"></i></div><div class="value">${money(owed)}</div><div class="label">Payable to vendors</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-truck-field"></i></div><div class="value">${vendors.length}</div><div class="label">Vendors</div></div></div>
      <div class="card">${tbl(['Bill', 'Vendor', 'Date', 'Due', '>Total', '>Balance', 'Status', ''], bills.map(b => `<tr data-id="${b.id}"><td><b>${esc(b.bill_no)}</b></td><td>${esc(b.vendor)}</td><td>${esc(b.date)}</td><td>${esc(b.due_date || '—')}</td><td class="right amount">${money(b.total)}</td><td class="right amount">${money(b.balance_due)}</td><td><span class="badge ${b.status === 'paid' ? 'locked' : b.status === 'void' ? 'none' : 'draft'}">${esc(b.status.replace('_', ' '))}</span></td>
        <td>${canBooks() && ['open', 'partially_paid'].includes(b.status) ? '<button class="ghost b-pay">Pay</button>' : ''}${canBooks() && b.status === 'open' && !b.payments.length ? '<button class="ghost b-void">Void</button>' : ''}</td></tr>`), 'No bills recorded yet.')}</div>`;
    panel.querySelectorAll('.b-pay').forEach(btn => btn.addEventListener('click', async e => {
      const b = bills.find(x => String(x.id) === e.target.closest('tr').dataset.id);
      const amt = prompt(`Pay how much? (balance ${money(b.balance_due)})`, b.balance_due); if (!amt) return;
      const ref = prompt('Payment reference / UTR (optional)', '') ?? '';
      try { await Apx.payBill(b.id, { amount: Number(amt), date: todayStr(), reference: ref }); toast('Payment recorded'); navigate('purchases'); } catch (er) { toast(er.message, true); } }));
    panel.querySelectorAll('.b-void').forEach(btn => btn.addEventListener('click', async e => { if (!confirm('Void this bill?')) return;
      try { await Apx.voidBill(e.target.closest('tr').dataset.id); navigate('purchases'); } catch (er) { toast(er.message, true); } }));
  }

  if (tab === 'newbill') {
    const row = () => `<div class="line-row bill-row"><input class="bl-desc" placeholder="Description" /><select class="bl-acct">${optList(spend, 'code', a => `${a.code} · ${a.name}`, '5700')}</select><input class="bl-amt" type="number" min="0" step="any" placeholder="Amount ₹" /><input class="bl-gst" type="number" min="0" max="40" step="any" value="18" title="GST %" /><i class="fa-solid fa-trash l-rm"></i></div>`;
    panel.innerHTML = `<div class="card"><h3>Record a vendor bill</h3><div class="form-grid">
      <label>Vendor<select id="bn-vendor">${optList(vendors, 'id', 'name')}</select></label><label>Vendor's bill no.<input id="bn-no" /></label><label>Bill date<input id="bn-date" type="date" value="${todayStr()}" /></label>
      <label>Due date (optional)<input id="bn-due" type="date" /></label><label>TDS deducted %<input id="bn-tds" type="number" min="0" step="any" value="0" /></label>
      <label>Input tax credit<select id="bn-itc"><option value="true">Eligible</option><option value="false">Not eligible</option></select></label></div>
      <h4 style="margin-top:1rem">Lines</h4><div id="bn-lines">${row()}</div><button class="ghost" id="bn-add" type="button"><i class="fa-solid fa-plus"></i> Add line</button>
      <div class="doc-total" id="bn-total"></div>
      <div class="actions-row"><button id="bn-save"><i class="fa-solid fa-check"></i> Save bill</button></div></div>`;
    const lines = () => [...panel.querySelectorAll('.bill-row')].map(r => ({ description: r.querySelector('.bl-desc').value, account_code: r.querySelector('.bl-acct').value, amount: Number(r.querySelector('.bl-amt').value || 0), gst_rate: Number(r.querySelector('.bl-gst').value || 0) }));
    const calc = () => { const l = lines(); const sub = l.reduce((s, x) => s + x.amount, 0), gst = l.reduce((s, x) => s + x.amount * x.gst_rate / 100, 0), tds = sub * Number(document.getElementById('bn-tds').value || 0) / 100;
      document.getElementById('bn-total').innerHTML = `<div><span>Subtotal</span><b>${money(sub)}</b></div><div><span>GST</span><b>${money(gst)}</b></div>${tds ? `<div><span>TDS deducted</span><b>− ${money(tds)}</b></div>` : ''}<div class="grand"><span>Payable to vendor</span><b>${money(sub + gst - tds)}</b></div>`; };
    const wire = (r) => { r.addEventListener('input', calc); r.querySelector('.l-rm').addEventListener('click', () => { if (panel.querySelectorAll('.bill-row').length > 1) { r.remove(); calc(); } }); };
    panel.querySelectorAll('.bill-row').forEach(wire); document.getElementById('bn-tds').addEventListener('input', calc); calc();
    document.getElementById('bn-add').addEventListener('click', () => { document.getElementById('bn-lines').insertAdjacentHTML('beforeend', row()); wire(document.getElementById('bn-lines').lastElementChild); });
    document.getElementById('bn-save').addEventListener('click', async () => {
      if (!canBooks()) return toast('Only Finance can record bills', true);
      try { const r = await Apx.createBill({ vendor_id: Number(document.getElementById('bn-vendor').value), bill_no: document.getElementById('bn-no').value, date: document.getElementById('bn-date').value, due_date: document.getElementById('bn-due').value || null, tds_rate: Number(document.getElementById('bn-tds').value || 0), itc_eligible: document.getElementById('bn-itc').value === 'true', lines: lines() });
        toast(`Bill saved — payable ${money(r.total)}`); state.purTab = 'bills'; navigate('purchases'); } catch (e) { toast(e.message, true); } });
  }

  if (tab === 'vendors') {
    panel.innerHTML = `${canBooks() ? '<div class="actions-row"><button id="v-new"><i class="fa-solid fa-plus"></i> New vendor</button></div>' : ''}<div id="v-form"></div>
      <div class="card">${tbl(['Vendor', 'GSTIN', 'State', 'Terms', 'TDS section', '>Outstanding', ''], vendors.map(v => `<tr data-id="${v.id}"><td><b>${esc(v.name)}</b><div class="muted" style="font-size:.74rem">${esc(v.email || '')}</div></td><td class="mono">${esc(v.gst || '—')}</td><td>${esc(v.state || v.state_code || '—')}</td><td>${v.payment_terms_days} days</td><td>${esc(v.tds_section || '—')}</td><td class="right amount">${money(v.outstanding)}</td><td>${canBooks() ? '<i class="fa-solid fa-pen v-edit" style="cursor:pointer"></i>' : ''}</td></tr>`), 'No vendors yet.')}</div>`;
    const form = (v) => { const c = v || { name: '', gst: '', pan: '', state: '', state_code: '', email: '', phone: '', address: '', payment_terms_days: 30, tds_section: '', bank_account: '', ifsc: '', status: 'active', notes: '' };
      const f = (k, l, t = 'text') => `<label>${l}<input data-k="${k}" type="${t}" value="${esc(c[k] ?? '')}" /></label>`;
      document.getElementById('v-form').innerHTML = `<div class="card"><h3>${v ? 'Edit' : 'New'} vendor</h3><div class="form-grid">${f('name', 'Name *')}${f('gst', 'GSTIN')}${f('pan', 'PAN')}${f('state', 'State')}${f('state_code', 'State code (e.g. 27)')}${f('email', 'Email')}${f('phone', 'Phone')}${f('address', 'Address')}${f('payment_terms_days', 'Payment terms (days)', 'number')}${f('tds_section', 'TDS section (e.g. 194C)')}${f('bank_account', 'Bank account')}${f('ifsc', 'IFSC')}</div>
        <div class="actions-row" style="margin-top:1rem"><button id="v-save">Save vendor</button><button class="ghost" id="v-cancel">Cancel</button></div></div>`;
      document.getElementById('v-cancel').onclick = () => { document.getElementById('v-form').innerHTML = ''; };
      document.getElementById('v-save').onclick = async () => { const d = { status: 'active', notes: c.notes || '' }; document.querySelectorAll('#v-form [data-k]').forEach(e => { d[e.dataset.k] = e.type === 'number' ? Number(e.value || 30) : e.value.trim(); });
        try { await Apx.saveVendor(v?.id, d); toast('Vendor saved'); navigate('purchases'); } catch (e) { toast(e.message, true); } }; };
    document.getElementById('v-new')?.addEventListener('click', () => form(null));
    panel.querySelectorAll('.v-edit').forEach(b => b.addEventListener('click', e => form(vendors.find(v => String(v.id) === e.target.closest('tr').dataset.id))));
  }
}

// ===========================================================================
// Accounting
// ===========================================================================
async function renderAccounting(content) {
  const tab = state.acTab || 'chart';
  const accts = await Apx.listAccounts();
  const tabs = [['chart', 'Chart of accounts', 'fa-list-tree'], ['journals', 'Journals', 'fa-book-open'], ['newje', 'New journal', 'fa-pen-to-square'], ['ledger', 'General ledger', 'fa-table-list'], ['lock', 'Period lock', 'fa-lock']];
  content.innerHTML = `${canBooks() ? '' : '<div class="alert-bar warn"><i class="fa-solid fa-lock"></i><span>Only Finance can change the books. You can view them.</span></div>'}${tabBar(tabs, tab)}<div id="ac-panel"></div>`;
  bindTabs(content, 'acTab', 'accounting');
  const panel = document.getElementById('ac-panel');

  if (tab === 'chart') {
    const types = ['asset', 'liability', 'equity', 'income', 'expense'];
    panel.innerHTML = `${canBooks() ? '<div class="actions-row"><button id="ca-new"><i class="fa-solid fa-plus"></i> New account</button></div>' : ''}<div id="ca-form"></div>
      <p class="muted">Postings from invoices, receipts, credit notes and bills appear here automatically — the ledger is always derived from those documents.</p>
      ${types.map(t => `<div class="card"><h3 style="text-transform:capitalize">${t}</h3>${tbl(['Code', 'Account', '>Balance', ''], accts.filter(a => a.type === t).map(a => `<tr data-id="${a.id}" class="${a.active ? '' : 'muted'}"><td class="mono">${esc(a.code)}</td><td>${esc(a.name)} ${a.system ? '<span class="badge none">system</span>' : ''}${a.active ? '' : ' <span class="badge none">inactive</span>'}</td><td class="right amount">${money(a.balance)}</td><td>${canBooks() ? '<i class="fa-solid fa-pen ca-edit" style="cursor:pointer"></i>' : ''}</td></tr>`), '—')}</div>`).join('')}`;
    const form = (a) => { document.getElementById('ca-form').innerHTML = `<div class="card"><h3>${a ? 'Edit' : 'New'} account</h3><div class="form-grid">
      <label>Code<input id="ca-code" value="${esc(a?.code || '')}" ${a ? 'disabled' : ''} placeholder="e.g. 5350" /></label><label>Name<input id="ca-name" value="${esc(a?.name || '')}" /></label>
      <label>Type<select id="ca-type" ${a?.system ? 'disabled' : ''}>${types.map(t => `<option ${a?.type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label></div>
      <div class="actions-row" style="margin-top:1rem"><button id="ca-save">Save</button><button class="ghost" id="ca-cancel">Cancel</button></div></div>`;
      document.getElementById('ca-cancel').onclick = () => { document.getElementById('ca-form').innerHTML = ''; };
      document.getElementById('ca-save').onclick = async () => { try { await Apx.saveAccount(a?.id, { code: a?.code || document.getElementById('ca-code').value, name: document.getElementById('ca-name').value, type: document.getElementById('ca-type').value, description: a?.description || '', active: a ? a.active : true }); toast('Account saved'); navigate('accounting'); } catch (e) { toast(e.message, true); } }; };
    document.getElementById('ca-new')?.addEventListener('click', () => form(null));
    panel.querySelectorAll('.ca-edit').forEach(b => b.addEventListener('click', e => form(accts.find(a => String(a.id) === e.target.closest('tr').dataset.id))));
  }

  if (tab === 'journals') {
    const js = await Apx.listJournals();
    panel.innerHTML = `<div class="card"><h3>Manual &amp; opening journals</h3>${js.length ? js.map(j => `<div class="je" data-id="${j.id}"><div class="flex-between"><div><b>${esc(j.ref)}</b> · ${esc(j.date)} · ${esc(j.memo || '')} ${j.kind !== 'manual' ? `<span class="badge none">${j.kind}</span>` : ''}${j.reversed ? ' <span class="badge draft">reversed</span>' : ''}</div>
      ${canBooks() && j.kind !== 'reversal' && !j.reversed ? '<button class="ghost je-rev">Reverse</button>' : ''}</div>
      ${tbl(['Account', '>Debit', '>Credit'], j.lines.map(l => `<tr><td class="mono">${esc(l.account_code)}</td><td>${esc(l.account)}</td><td class="right amount">${l.debit ? money(l.debit) : ''}</td><td class="right amount">${l.credit ? money(l.credit) : ''}</td></tr>`).join('').replace(/<td class="mono">(.*?)<\/td><td>(.*?)<\/td>/g, '<td>$1 · $2</td>').split('</tr>').filter(Boolean).map(x => x + '</tr>'), '')}</div>`).join('') : '<p class="muted">No manual journals yet. Use New journal for opening balances and adjustments.</p>'}</div>`;
    panel.querySelectorAll('.je-rev').forEach(b => b.addEventListener('click', async e => { if (!confirm('Post a reversing entry dated today?')) return; try { await Apx.reverseJournal(e.target.closest('.je').dataset.id); toast('Reversed'); navigate('accounting'); } catch (er) { toast(er.message, true); } }));
  }

  if (tab === 'newje') {
    const active = accts.filter(a => a.active);
    const row = () => `<div class="line-row je-row"><select class="j-acct">${optList(active, 'code', a => `${a.code} · ${a.name}`)}</select><input class="j-dr" type="number" min="0" step="any" placeholder="Debit" /><input class="j-cr" type="number" min="0" step="any" placeholder="Credit" /><input class="j-note" placeholder="Note" /><i class="fa-solid fa-trash l-rm"></i></div>`;
    panel.innerHTML = `<div class="card"><h3>New journal entry</h3><div class="form-grid"><label>Date<input type="date" id="je-date" value="${todayStr()}" /></label><label>Type<select id="je-kind"><option value="manual">Adjustment</option><option value="opening">Opening balance</option></select></label><label>Memo<input id="je-memo" /></label></div>
      <h4 style="margin-top:1rem">Lines</h4><div id="je-lines">${row()}${row()}</div><button class="ghost" id="je-add" type="button"><i class="fa-solid fa-plus"></i> Add line</button><div class="doc-total" id="je-total"></div>
      <div class="actions-row"><button id="je-save" ${canBooks() ? '' : 'disabled title="Finance only"'}><i class="fa-solid fa-check"></i> Post journal</button></div></div>`;
    const calc = () => { const dr = [...panel.querySelectorAll('.j-dr')].reduce((s, e) => s + Number(e.value || 0), 0), cr = [...panel.querySelectorAll('.j-cr')].reduce((s, e) => s + Number(e.value || 0), 0);
      document.getElementById('je-total').innerHTML = `<div><span>Debits</span><b>${money(dr)}</b></div><div><span>Credits</span><b>${money(cr)}</b></div><div class="grand ${Math.abs(dr - cr) < 0.005 && dr ? 'ok' : 'bad'}"><span>${Math.abs(dr - cr) < 0.005 && dr ? 'Balanced' : 'Difference'}</span><b>${money(Math.abs(dr - cr))}</b></div>`; };
    const wire = (r) => { r.addEventListener('input', calc); r.querySelector('.l-rm').addEventListener('click', () => { if (panel.querySelectorAll('.je-row').length > 2) { r.remove(); calc(); } }); };
    panel.querySelectorAll('.je-row').forEach(wire); calc();
    document.getElementById('je-add').addEventListener('click', () => { document.getElementById('je-lines').insertAdjacentHTML('beforeend', row()); wire(document.getElementById('je-lines').lastElementChild); });
    document.getElementById('je-save').addEventListener('click', async () => {
      const lines = [...panel.querySelectorAll('.je-row')].map(r => ({ account_code: r.querySelector('.j-acct').value, debit: Number(r.querySelector('.j-dr').value || 0), credit: Number(r.querySelector('.j-cr').value || 0), note: r.querySelector('.j-note').value })).filter(l => l.debit || l.credit);
      try { await Apx.createJournal({ date: document.getElementById('je-date').value, kind: document.getElementById('je-kind').value, memo: document.getElementById('je-memo').value, lines }); toast('Journal posted'); state.acTab = 'journals'; navigate('accounting'); } catch (e) { toast(e.message, true); } });
  }

  if (tab === 'ledger') {
    panel.innerHTML = `<div class="card"><div class="actions-row"><select id="gl-acct">${optList(accts, 'code', a => `${a.code} · ${a.name}`, state.glAcct || '1100')}</select><input type="date" id="gl-start" value="${fyStart()}" /><input type="date" id="gl-end" value="${todayStr()}" /><button id="gl-go">Show</button></div><div id="gl-out"></div></div>`;
    const go = async () => { state.glAcct = document.getElementById('gl-acct').value;
      try { const r = await Apx.ledger(state.glAcct, document.getElementById('gl-start').value, document.getElementById('gl-end').value);
        document.getElementById('gl-out').innerHTML = `<p><b>${esc(r.account.code)} · ${esc(r.account.name)}</b> — opening ${money(r.opening)} · closing <b>${money(r.closing)}</b></p>${tbl(['Date', 'Reference', 'Description', 'Source', '>Debit', '>Credit', '>Balance'], r.lines.map(l => `<tr><td>${esc(l.date)}</td><td class="mono">${esc(l.ref)}</td><td style="white-space:normal">${esc(l.desc)}</td><td class="muted">${esc(l.source)}</td><td class="right amount">${l.debit ? money(l.debit) : ''}</td><td class="right amount">${l.credit ? money(l.credit) : ''}</td><td class="right amount">${money(l.balance)}</td></tr>`), 'No transactions in this period.')}`;
      } catch (e) { toast(e.message, true); } };
    document.getElementById('gl-go').addEventListener('click', go); go();
  }

  if (tab === 'lock') {
    const { lock_date } = await Apx.getLock();
    panel.innerHTML = `<div class="card"><h3>Lock the books</h3><p class="muted">No bills, bill payments or journals can be created or voided on or before the lock date — use it after you close a month or file GST returns. Invoices and receipts follow their own workflow.</p>
      <div class="actions-row"><input type="date" id="lk-date" value="${esc(lock_date)}" /><button id="lk-save" ${canBooks() ? '' : 'disabled title="Finance only"'}>Save lock date</button><button class="ghost" id="lk-clear" ${canBooks() ? '' : 'disabled'}>Unlock</button></div>
      <p>${lock_date ? `<span class="badge locked">Locked up to ${esc(lock_date)}</span>` : '<span class="badge none">Books are open</span>'}</p></div>`;
    document.getElementById('lk-save').addEventListener('click', async () => { try { await Apx.setLock(document.getElementById('lk-date').value); toast('Lock date saved'); navigate('accounting'); } catch (e) { toast(e.message, true); } });
    document.getElementById('lk-clear').addEventListener('click', async () => { try { await Apx.setLock(''); toast('Books unlocked'); navigate('accounting'); } catch (e) { toast(e.message, true); } });
  }
}

// ===========================================================================
// Financial reports
// ===========================================================================
const REPORTS = {
  'trial-balance': { label: 'Trial balance', dates: 'asof' },
  'profit-loss': { label: 'Profit & loss', dates: 'range' },
  'balance-sheet': { label: 'Balance sheet', dates: 'asof' },
  'aged-receivables': { label: 'Aged receivables', dates: 'none' },
  'aged-payables': { label: 'Aged payables', dates: 'none' },
  'sales-by-customer': { label: 'Sales by customer', dates: 'range' },
  'sales-by-item': { label: 'Sales by item', dates: 'range' },
  'gst': { label: 'GST (GSTR-1 / 3B)', dates: 'range-month' },
};

async function renderFinReports(content) {
  const key = state.finReport || 'trial-balance';
  const meta = REPORTS[key];
  const ig = await Apx.integrity().catch(() => null);
  const monthStart = todayStr().slice(0, 8) + '01';
  content.innerHTML = `
    ${ig ? `<div class="card integrity ${ig.ok ? 'ok' : 'bad'}"><div class="flex-between"><h3><i class="fa-solid ${ig.ok ? 'fa-shield-halved' : 'fa-triangle-exclamation'}"></i> Books integrity — ${ig.ok ? 'all checks pass' : 'attention needed'}</h3></div>
      <div class="checks">${ig.checks.map(c => `<div><i class="fa-solid ${c.ok ? 'fa-circle-check' : 'fa-circle-xmark'}"></i> <span>${esc(c.name)}</span> <span class="muted">${esc(c.detail)}</span></div>`).join('')}</div></div>` : ''}
    <div class="card"><div class="actions-row">
      <select id="fr-type">${Object.entries(REPORTS).map(([k, v]) => `<option value="${k}" ${k === key ? 'selected' : ''}>${v.label}</option>`).join('')}</select>
      ${meta.dates === 'asof' ? `<label class="inline-check">As of <input type="date" id="fr-end" value="${todayStr()}" /></label>` : ''}
      ${meta.dates.startsWith('range') ? `<label class="inline-check">From <input type="date" id="fr-start" value="${meta.dates === 'range-month' ? monthStart : fyStart()}" /></label><label class="inline-check">To <input type="date" id="fr-end" value="${todayStr()}" /></label>` : ''}
      <button id="fr-go">Run</button><button class="ghost" id="fr-csv"><i class="fa-solid fa-file-csv"></i> CSV</button>
      ${key === 'gst' ? '<a class="btn secondary" id="fr-xlsx" href="#"><i class="fa-solid fa-file-excel"></i> GSTR Excel</a>' : ''}</div>
      <div id="fr-out"><p class="muted">Loading…</p></div></div>`;
  document.getElementById('fr-type').addEventListener('change', e => { state.finReport = e.target.value; navigate('finreports'); });
  let csv = [];
  const qs = () => { const s = document.getElementById('fr-start')?.value, e = document.getElementById('fr-end')?.value; return meta.dates === 'asof' ? `as_of=${e}` : meta.dates === 'none' ? '' : `start=${s}&end=${e}`; };
  const sec = (title, rows, totalLabel, total) => `<h4>${title}</h4>${tbl(['Account', '>Amount'], rows.map(r => `<tr><td>${esc(r.code ? r.code + ' · ' : '')}${esc(r.name)}</td><td class="right amount">${money(r.amount)}</td></tr>`).concat(totalLabel ? [`<tr class="total-row"><td>${totalLabel}</td><td class="right amount">${money(total)}</td></tr>`] : []), '—')}`;
  const run = async () => {
    const out = document.getElementById('fr-out');
    try {
      const r = await Apx.finReport(key, qs());
      if (key === 'trial-balance') { csv = [['Code', 'Account', 'Debit', 'Credit'], ...r.lines.map(l => [l.code, l.name, l.debit, l.credit])];
        out.innerHTML = `<p>As of ${esc(r.as_of)} — ${r.balanced ? '<span class="badge locked">balanced</span>' : '<span class="badge error">out of balance</span>'}</p>${tbl(['Code', 'Account', '>Debit', '>Credit'], r.lines.map(l => `<tr><td class="mono">${esc(l.code)}</td><td>${esc(l.name)}</td><td class="right amount">${l.debit ? money(l.debit) : ''}</td><td class="right amount">${l.credit ? money(l.credit) : ''}</td></tr>`).concat([`<tr class="total-row"><td></td><td>Total</td><td class="right amount">${money(r.total_debit)}</td><td class="right amount">${money(r.total_credit)}</td></tr>`]), '—')}`; }
      else if (key === 'profit-loss') { csv = [['Section', 'Account', 'Amount'], ...r.income.map(x => ['Income', x.name, x.amount]), ...r.expense.map(x => ['Expense', x.name, x.amount]), ['', 'Net profit', r.net_profit]];
        out.innerHTML = `${sec('Income', r.income, 'Total income', r.total_income)}${sec('Expenses', r.expense, 'Total expenses', r.total_expense)}<div class="doc-total"><div class="grand ${r.net_profit >= 0 ? 'ok' : 'bad'}"><span>Net ${r.net_profit >= 0 ? 'profit' : 'loss'}</span><b>${money(r.net_profit)}</b></div></div>`; }
      else if (key === 'balance-sheet') { csv = [['Section', 'Account', 'Amount'], ...r.asset.map(x => ['Asset', x.name, x.amount]), ...r.liability.map(x => ['Liability', x.name, x.amount]), ...r.equity.map(x => ['Equity', x.name, x.amount])];
        out.innerHTML = `<p>As of ${esc(r.as_of)} — ${r.balanced ? '<span class="badge locked">balanced</span>' : '<span class="badge error">out of balance</span>'}</p><div class="grid-2"><div>${sec('Assets', r.asset, 'Total assets', r.total_assets)}</div><div>${sec('Liabilities', r.liability, 'Total liabilities', r.total_liabilities)}${sec('Equity', r.equity, 'Total equity', r.total_equity)}</div></div>`; }
      else if (key === 'aged-receivables' || key === 'aged-payables') { csv = [['Party', 'Current', '1-30', '31-60', '61-90', '90+', 'Total'], ...r.rows.map(x => [x.party, x.current, x.d1_30, x.d31_60, x.d61_90, x.d90_plus, x.total])];
        out.innerHTML = `<p>${r.reconciled ? '<span class="badge locked">agrees with ledger</span>' : '<span class="badge error">differs from ledger</span>'} <span class="muted">ledger ${money(r.ledger_balance)}</span></p>${tbl([key === 'aged-payables' ? 'Vendor' : 'Customer', '>Current', '>1–30', '>31–60', '>61–90', '>90+', '>Total'], r.rows.map(x => `<tr><td>${esc(x.party)}</td>${['current', 'd1_30', 'd31_60', 'd61_90', 'd90_plus', 'total'].map(k => `<td class="right amount">${x[k] ? money(x[k]) : '—'}</td>`).join('')}</tr>`).concat([`<tr class="total-row"><td>Total</td>${['current', 'd1_30', 'd31_60', 'd61_90', 'd90_plus', 'total'].map(k => `<td class="right amount">${money(r.totals[k])}</td>`).join('')}</tr>`]), 'Nothing outstanding.')}`; }
      else if (key === 'sales-by-customer') { csv = [['Customer', 'Invoices', 'Taxable', 'GST', 'Total'], ...r.rows.map(x => [x.customer, x.invoices, x.taxable, x.gst, x.total])];
        out.innerHTML = tbl(['Customer', '>Invoices', '>Taxable', '>GST', '>Total'], r.rows.map(x => `<tr><td>${esc(x.customer)}</td><td class="right">${x.invoices}</td><td class="right amount">${money(x.taxable)}</td><td class="right amount">${money(x.gst)}</td><td class="right amount">${money(x.total)}</td></tr>`), 'No sales in this period.'); }
      else if (key === 'sales-by-item') { csv = [['Item', 'Quantity', 'Taxable'], ...r.rows.map(x => [x.item, x.qty, x.taxable])];
        out.innerHTML = tbl(['Item / line', '>Quantity', '>Taxable value'], r.rows.map(x => `<tr><td>${esc(x.item)}</td><td class="right">${x.qty}</td><td class="right amount">${money(x.taxable)}</td></tr>`), 'No sales in this period.'); }
      else if (key === 'gst') { const s = r.summary; csv = [['Table', 'Party', 'Document', 'Date', 'Rate %', 'Taxable', 'IGST', 'CGST', 'SGST'], ...r.b2b.map(x => ['B2B', x.customer, x.invoice_no, x.date, x.rate, x.taxable, x.igst, x.cgst, x.sgst]), ...r.cdn.map(x => ['Notes', x.customer, x.invoice_no, x.date, x.rate, x.taxable, x.igst, x.cgst, x.sgst])];
        const x = document.getElementById('fr-xlsx'); if (x) x.href = Apx.gstExportUrl(document.getElementById('fr-start').value, document.getElementById('fr-end').value);
        out.innerHTML = `<h4>GSTR-3B summary</h4>${tbl(['', '>IGST', '>CGST', '>SGST'], [`<tr><td>Outward taxable value</td><td class="right amount" colspan="3">${money(s.outward.taxable)}</td></tr>`, `<tr><td>Output tax</td>${['igst', 'cgst', 'sgst'].map(k => `<td class="right amount">${money(s.outward[k])}</td>`).join('')}</tr>`, `<tr><td>Input tax credit (from bills)</td>${['igst', 'cgst', 'sgst'].map(k => `<td class="right amount">− ${money(s.itc[k])}</td>`).join('')}</tr>`, `<tr class="total-row"><td>Net tax payable</td>${['igst', 'cgst', 'sgst'].map(k => `<td class="right amount">${money(s.net_payable[k])}</td>`).join('')}</tr>`], '')}
          ${s.itc.ineligible ? `<p class="muted">${money(s.itc.ineligible)} of input GST on bills marked "not eligible" was excluded from credit.</p>` : ''}
          <h4>B2B invoices (${r.b2b.length})</h4>${tbl(['GSTIN', 'Customer', 'Invoice', 'Date', '>Rate', '>Taxable', '>IGST', '>CGST', '>SGST'], r.b2b.map(b => `<tr><td class="mono">${esc(b.gstin)}</td><td>${esc(b.customer)}</td><td>${esc(b.invoice_no)}</td><td>${esc(b.date)}</td><td class="right">${b.rate}%</td><td class="right amount">${money(b.taxable)}</td><td class="right amount">${money(b.igst)}</td><td class="right amount">${money(b.cgst)}</td><td class="right amount">${money(b.sgst)}</td></tr>`), 'No B2B invoices in this period.')}
          <h4>Credit &amp; debit notes (${r.cdn.length})</h4>${tbl(['Type', 'Customer', 'Document', 'Date', '>Taxable', '>Tax'], r.cdn.map(n => `<tr><td>${n.note_type}</td><td>${esc(n.customer)}</td><td>${esc(n.invoice_no)}</td><td>${esc(n.date)}</td><td class="right amount">${money(n.taxable)}</td><td class="right amount">${money(n.igst + n.cgst + n.sgst)}</td></tr>`), 'None.')}
          <h4>HSN / SAC summary</h4>${tbl(['HSN / SAC', '>Rate', '>Quantity', '>Taxable'], r.hsn.map(h => `<tr><td class="mono">${esc(h.hsn_sac)}</td><td class="right">${h.rate}%</td><td class="right">${h.qty}</td><td class="right amount">${money(h.taxable)}</td></tr>`), 'None.')}`; }
    } catch (e) { out.innerHTML = `<p style="color:var(--danger)">${esc(e.message)}</p>`; }
  };
  document.getElementById('fr-go').addEventListener('click', run);
  document.getElementById('fr-csv').addEventListener('click', () => csv.length ? csvDownload(`${key}_${todayStr()}.csv`, csv) : toast('Run the report first', true));
  run();
}

const MODULE_RENDERERS = { items: renderItems, sales: renderSales, purchases: renderPurchases, accounting: renderAccounting, finreports: renderFinReports };
