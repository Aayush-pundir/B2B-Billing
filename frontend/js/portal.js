// Public customer portal — no login; access is by the unguessable token in the link.
const token = new URLSearchParams(location.search).get('t') || '';
const root = document.getElementById('portal');
const esc = (s) => (s ?? '').toString().replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = (d) => d ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

function toast(msg, isError) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.innerHTML = `<i class="fa-solid ${isError ? 'fa-circle-exclamation' : 'fa-circle-check'}"></i><span></span>`;
  t.querySelector('span').textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), 3800);
}

async function api(path, body) {
  const res = await fetch(`/api/portal/${encodeURIComponent(token)}${path}`, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Something went wrong'); }
  return res.json();
}

async function load() {
  let d;
  try { d = await api(''); }
  catch (e) {
    root.innerHTML = '<div class="card p-error"><i class="fa-regular fa-file-excel"></i><h3>This link isn\'t valid</h3><p class="muted">The invoice link may be incorrect or no longer active. Please contact the sender.</p></div>';
    return;
  }
  const inv = d.invoice, pay = d.pay;
  const paid = inv.status === 'paid' || inv.balance_due <= 0;
  const overdue = !paid && inv.due_date && inv.due_date < new Date().toISOString().slice(0, 10);
  document.title = `Invoice ${inv.invoice_no} — ${d.org.name}`;
  const hasPay = pay.bank_name || pay.account_no || pay.upi_id;

  root.innerHTML = `
    <div class="p-head"><span class="logo-mark"><i class="fa-solid fa-file-invoice"></i></span><div><b>${esc(d.org.name)}</b><span>Invoice for ${esc(d.customer.name)}</span></div></div>

    <div class="card p-hero">
      <div class="lbl">${paid ? 'Paid in full' : 'Amount due'}</div>
      <div class="amt">${money(paid ? inv.total : inv.balance_due)}</div>
      <div class="meta">
        <span class="badge ${paid ? 'locked' : overdue ? 'error' : 'sent'}">${paid ? 'Paid' : overdue ? 'Overdue' : 'Open'}</span>
        <span class="badge none">${esc(inv.invoice_no)}</span>
        <span class="badge none">Period ${esc(inv.period)}</span>
        ${paid ? '' : `<span class="badge none">Due ${fmtDate(inv.due_date)}</span>`}
      </div>
      ${paid ? '<div class="p-state paid"><i class="fa-solid fa-circle-check"></i><div>Thank you — this invoice has been paid.</div></div>' : ''}
      ${inv.disputed ? `<div class="p-state query"><i class="fa-solid fa-comment-dots"></i><div>You raised a query on this invoice: “${esc(inv.dispute_reason)}”. We'll get back to you shortly.</div></div>` : ''}
      ${inv.promise_date && !paid ? `<div class="p-state promise"><i class="fa-solid fa-calendar-check"></i><div>You told us you'll pay on <b>${fmtDate(inv.promise_date)}</b>${inv.promise_note ? ` — “${esc(inv.promise_note)}”` : ''}.</div></div>` : ''}
      <div class="p-actions"><a class="btn ghost" href="/api/portal/${encodeURIComponent(token)}/pdf" target="_blank"><i class="fa-solid fa-download"></i> Download PDF</a></div>
    </div>

    ${!paid && hasPay ? `
    <div class="card"><h3><i class="fa-solid fa-wallet"></i> How to pay</h3>
      <div class="pay-grid">
        <dl class="kv">
          ${pay.bank_name ? `<dt>Bank</dt><dd>${esc(pay.bank_name)}</dd>` : ''}
          ${pay.account_no ? `<dt>Account no.</dt><dd>${esc(pay.account_no)} <button class="copy" data-copy="${esc(pay.account_no)}"><i class="fa-regular fa-copy"></i></button></dd>` : ''}
          ${pay.ifsc ? `<dt>IFSC</dt><dd>${esc(pay.ifsc)} <button class="copy" data-copy="${esc(pay.ifsc)}"><i class="fa-regular fa-copy"></i></button></dd>` : ''}
          ${pay.upi_id ? `<dt>UPI ID</dt><dd>${esc(pay.upi_id)} <button class="copy" data-copy="${esc(pay.upi_id)}"><i class="fa-regular fa-copy"></i></button></dd>` : ''}
          <dt>Reference</dt><dd>${esc(pay.reference)} <button class="copy" data-copy="${esc(pay.reference)}"><i class="fa-regular fa-copy"></i></button></dd>
        </dl>
        ${pay.upi_link ? '<div id="qr"></div>' : ''}
      </div>
      ${pay.upi_link ? `<div class="p-actions"><a class="btn" href="${esc(pay.upi_link)}"><i class="fa-solid fa-mobile-screen"></i> Pay ${money(inv.balance_due)} with UPI</a></div>` : ''}
      <p class="muted" style="font-size:.78rem;margin-bottom:0">Please quote the reference when paying so we can match your payment automatically.</p>
    </div>` : ''}

    <div class="card"><h3>Invoice details</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Description</th><th class="right">Amount</th></tr></thead>
        <tbody>${inv.lines.map(l => `<tr><td style="white-space:normal"><b>${esc(l.label)}</b><div class="muted" style="font-size:.76rem">${esc(l.calc_detail)}</div></td><td class="right amount">${money(l.amount)}</td></tr>`).join('')}</tbody>
      </table></div>
      <div class="totals">
        <div><span>Subtotal</span><span class="amount">${money(inv.subtotal)}</span></div>
        <div><span>${esc(inv.gst_type.replace('_', ' + '))}</span><span class="amount">${money(inv.gst)}</span></div>
        ${inv.tds ? `<div><span>TDS</span><span class="amount">− ${money(inv.tds)}</span></div>` : ''}
        <div class="grand"><span>Total</span><span class="amount">${money(inv.total)}</span></div>
        ${inv.paid_amount ? `<div><span>Paid</span><span class="amount">− ${money(inv.paid_amount)}</span></div>` : ''}
      </div>
      ${d.payments.length ? `<h4 style="margin-top:1rem">Payments received</h4>${d.payments.map(p => `<div class="flex-between" style="font-size:.86rem;padding:.2rem 0"><span>${fmtDate(p.date)} · ${esc(p.mode || 'payment')}</span><span class="amount">${money(p.amount)}</span></div>`).join('')}` : ''}
    </div>

    ${!paid ? `
    <div class="card"><h3><i class="fa-solid fa-calendar-check"></i> Let us know when you'll pay</h3>
      <p class="muted">Can't pay by the due date? Tell us when to expect it and we'll pause reminders until then.</p>
      <form class="p-form" id="f-promise"><div class="row"><input type="date" id="pr-date" required /><input id="pr-note" placeholder="Note (optional)" maxlength="300" /></div>
        <div><button type="submit"><i class="fa-solid fa-check"></i> Confirm payment date</button></div></form>
    </div>
    ${inv.disputed ? '' : `<div class="card"><h3><i class="fa-solid fa-comment-dots"></i> Something look wrong?</h3>
      <p class="muted">Raise a query and our team will review it. Reminders are paused while it's open.</p>
      <form class="p-form" id="f-dispute"><textarea id="dp-reason" rows="3" placeholder="What's the issue with this invoice?" required maxlength="500"></textarea>
        <div><button type="submit" class="ghost"><i class="fa-solid fa-paper-plane"></i> Send query</button></div></form>
    </div>`}` : ''}

    ${d.other_open.length ? `<div class="card"><h3>Other open invoices</h3>${d.other_open.map(o => `<div class="flex-between" style="font-size:.86rem;padding:.3rem 0"><span>${esc(o.invoice_no)} <span class="muted">· due ${fmtDate(o.due_date)}</span></span><span class="amount">${money(o.balance_due)}</span></div>`).join('')}</div>` : ''}
    <div class="p-foot">Secure invoice link · ${esc(d.org.name)}${d.org.gst ? ` · GSTIN ${esc(d.org.gst)}` : ''}</div>`;

  if (pay.upi_link && typeof QRCode !== 'undefined') new QRCode(document.getElementById('qr'), { text: pay.upi_link, width: 150, height: 150 });
  else document.getElementById('qr')?.remove();
  root.querySelectorAll('.copy').forEach(b => b.addEventListener('click', async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast('Copied'); } catch (e) { toast('Copy failed', true); } }));
  const dateEl = document.getElementById('pr-date');
  if (dateEl) { const t = new Date(); dateEl.min = t.toISOString().slice(0, 10); t.setDate(t.getDate() + 90); dateEl.max = t.toISOString().slice(0, 10); }
  document.getElementById('f-promise')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await api('/promise', { date: dateEl.value, note: document.getElementById('pr-note').value }); toast('Thanks — we have noted your payment date'); load(); }
    catch (err) { toast(err.message, true); }
  });
  document.getElementById('f-dispute')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await api('/dispute', { reason: document.getElementById('dp-reason').value }); toast('Your query has been sent'); load(); }
    catch (err) { toast(err.message, true); }
  });
}
load();
