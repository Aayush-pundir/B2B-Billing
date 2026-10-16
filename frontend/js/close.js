// ---------------------------------------------------------------------------
// Month close — one guided flow: check inputs → calculate → review → lock → push to Zoho
// ---------------------------------------------------------------------------

const CLOSE_STEPS = [['inputs', 'Check inputs'], ['calc', 'Calculate'], ['review', 'Review'], ['lock', 'Lock'], ['zoho', 'Zoho']];
const CLOSE_STATUS_ICON = { ok: 'fa-circle-check', warn: 'fa-triangle-exclamation', block: 'fa-circle-xmark' };

function closeLastMonth() {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

async function renderClose(content) {
  const c = state.close = Object.assign({ period: closeLastMonth(), tab: 'needs_review', reason: '', sel: new Set(), open: new Set() }, state.close || {});
  let timer = null;
  const stop = () => { if (timer) { clearInterval(timer); timer = null; } };

  async function load(keepScroll) {
    stop();
    const y = window.scrollY;
    const chk = await Apx.closeApi(`/checklist?period=${c.period}`);
    const run = chk.run;
    const q = run ? await Apx.closeApi(`/runs/${run.id}/review`) : null;
    const locked = run && ['approved', 'pushed'].includes(run.status);
    const z = locked ? await Apx.closeApi(`/runs/${run.id}/zoho/status`) : null;
    draw(chk, run, q, z);
    if (keepScroll) window.scrollTo(0, y);
    if (z && z.running) timer = setInterval(async () => {
      if (!content.isConnected) return stop();
      const next = await Apx.closeApi(`/runs/${run.id}/zoho/status`);
      if (!next.running) { stop(); load(true); } else paintZohoProgress(next);
    }, 1500);
  }

  function currentStep(run, q, z) {
    if (!run) return 'calc';
    if (run.status === 'pending_approval') return q.counts.open > 0 ? 'review' : 'lock';
    if (run.status === 'pushed' || (z && z.total && z.pushed === z.total)) return 'done';
    return 'zoho';
  }

  function stepper(cur) {
    const at = CLOSE_STEPS.findIndex(s => s[0] === cur);
    return `<ol class="stepper">${CLOSE_STEPS.map(([id, label], i) => {
      const cls = cur === 'done' || i < at ? 'done' : i === at ? 'now' : '';
      return `<li class="${cls}"><span class="dot">${cls === 'done' ? '<i class="fa-solid fa-check"></i>' : i + 1}</span>${label}</li>`;
    }).join('')}</ol>`;
  }

  function card(id, n, title, summary, body, cur, doneWhen) {
    const isDone = doneWhen, isNow = cur === id;
    return `<details class="step-card ${isDone ? 'done' : ''} ${isNow ? 'now' : ''}" ${isNow || c.open.has(id) ? 'open' : ''} data-step="${id}">
      <summary><span class="dot">${isDone ? '<i class="fa-solid fa-check"></i>' : n}</span><b>${title}</b><span class="muted sum">${summary}</span></summary>
      <div class="step-body">${body}</div></details>`;
  }

  function draw(chk, run, q, z) {
    const cur = currentStep(run, q, z);
    const body = content.querySelector('#close-body');
    const blockers = chk.items.filter(i => i.status === 'block').length, warns = chk.items.filter(i => i.status === 'warn').length;

    const inputs = `<div class="checks">${chk.items.map(i => `<div class="check-row ${i.status}"><i class="fa-solid ${CLOSE_STATUS_ICON[i.status]}"></i>
        <div><b>${esc(i.label)}</b><div class="muted">${esc(i.detail)}</div></div>${i.page ? `<a class="btn ghost" href="#${i.page}">Open</a>` : ''}</div>`).join('')}</div>
      ${blockers ? '<p class="muted">Accounts with a ⛔ are withheld from the run, never billed at ₹0 by mistake. Fix them and recalculate next month, or bill them by hand.</p>' : ''}`;

    let calc;
    if (!run) {
      calc = `<p class="muted">${chk.billable_accounts} accounts are billable in ${esc(c.period)}. Usage is read straight from the platform database; nothing to upload.</p>
        <div class="actions-row"><button id="calc-go" ${isFinance() || canBill() ? '' : 'disabled'}><i class="fa-solid fa-calculator"></i> Calculate ${esc(c.period)}</button>
        <a class="btn ghost" href="#billing">Use CSV files instead</a></div>`;
    } else {
      const w = q.run.withheld || [];
      calc = `<div class="stat-grid mini">
          <div class="stat-card"><div class="value">${q.run.invoices}</div><div class="label">Invoices</div></div>
          <div class="stat-card"><div class="value">${money(q.run.total)}</div><div class="label">Taxable total</div></div>
          <div class="stat-card"><div class="value">${w.length}</div><div class="label">Withheld</div></div>
          <div class="stat-card"><div class="value">${q.run.anomalies.filter(a => a.severity === 'high').length}</div><div class="label">High findings</div></div></div>
        <p class="muted">Calculated by ${esc(q.run.created_by || 'the scheduler')}. ${w.length ? 'Withheld accounts need fixing before they can be billed:' : ''}</p>
        ${w.length ? `<details><summary>${w.length} withheld</summary><ul class="plain">${w.map(x => `<li><b>${esc(x.enterprise_id)}</b> — ${esc(x.reason)}</li>`).join('')}</ul></details>` : ''}`;
    }

    let rev = '<p class="muted">Calculate first.</p>', revSum = '';
    if (run && q) {
      const k = q.counts, done = k.total - k.open;
      revSum = `${done} of ${k.total} checked`;
      const canReview = run.status === 'pending_approval';
      const reasons = [...new Set(q.items.flatMap(i => i.reasons.map(r => r.code.startsWith('exception:') ? 'exception' : r.code)))];
      const tabCounts = { needs_review: q.items.filter(i => i.status === 'needs_review' && i.kind === 'flag').length, spot: q.items.filter(i => i.status === 'needs_review' && i.kind === 'spot').length, done: q.items.filter(i => i.status !== 'needs_review').length };
      const shown = q.items.filter(i => (c.tab === 'needs_review' ? i.status === 'needs_review' && i.kind === 'flag' : c.tab === 'spot' ? i.status === 'needs_review' && i.kind === 'spot' : i.status !== 'needs_review')
        && (!c.reason || i.reasons.some(r => (r.code.startsWith('exception:') ? 'exception' : r.code) === c.reason)));
      rev = `<div class="progress"><span style="width:${k.total ? done / k.total * 100 : 100}%"></span></div>
        <div class="actions-row"><div class="seg">
          ${[['needs_review', 'Needs a check'], ['spot', 'Spot-checks'], ['done', 'Checked']].map(([id, label]) => `<button class="seg-btn ${c.tab === id ? 'on' : ''}" data-tab="${id}">${label} <span>${tabCounts[id]}</span></button>`).join('')}</div>
          <select id="rv-reason"><option value="">Any reason</option>${reasons.map(r => `<option value="${r}" ${c.reason === r ? 'selected' : ''}>${esc(closeReasonName(r))}</option>`).join('')}</select>
          ${canReview && shown.some(i => i.status === 'needs_review') ? `<button id="rv-bulk" class="secondary"><i class="fa-solid fa-check-double"></i> Mark ${c.sel.size ? c.sel.size + ' selected' : 'all shown'} as checked</button>` : ''}</div>
        <div class="table-wrap"><table class="rv">
          <thead><tr>${canReview ? '<th></th>' : ''}<th>Customer</th><th class="right">This month</th><th class="right">Last month</th><th>Why it is here</th><th></th></tr></thead>
          <tbody>${shown.map(i => reviewRow(i, canReview)).join('') || `<tr><td colspan="6" class="muted">${c.tab === 'done' ? 'Nothing checked yet.' : 'Nothing here — all clear.'}</td></tr>`}</tbody></table></div>`;
    }

    let lock = '<p class="muted">Review comes first.</p>', lockSum = '';
    if (run && q) {
      const high = q.run.anomalies.filter(a => a.severity === 'high');
      if (run.status === 'pending_approval') {
        const self = state.user && state.user.email === q.run.created_by;
        lock = `<p>Locking freezes the usage figures and fixes every invoice at ${money(q.run.total)} taxable. ${self ? '<b>You calculated this run, so a second Finance person has to lock it.</b>' : ''}</p>
          ${high.length ? `<label class="inline-check"><input type="checkbox" id="lk-ack" /> I have read the ${high.length} high-severity finding(s) <button class="ghost" id="lk-show" type="button">show</button></label><ul class="plain" id="lk-list" hidden>${high.map(a => `<li><b>${esc(a.enterprise_id)}</b> ${esc(a.message)}</li>`).join('')}</ul>` : ''}
          <div class="actions-row"><button id="lk-go" ${q.counts.open || !isFinance() ? 'disabled' : ''}><i class="fa-solid fa-lock"></i> Lock ${esc(c.period)}</button>
          ${isFinance() ? '<button class="ghost danger" id="lk-reject">Send back…</button>' : '<span class="muted">Only Finance can lock.</span>'}
          ${q.counts.open ? `<span class="muted">${q.counts.open} still to check</span>` : ''}</div>`;
      } else {
        lockSum = `locked by ${esc(q.run.approved_by)}`;
        lock = `<p>Locked by <b>${esc(q.run.approved_by)}</b>. Invoices are final${run.status === 'pushed' ? ' and in Zoho' : ''}.</p>`;
      }
    }

    let zoho = '<p class="muted">Lock the run first.</p>', zSum = '';
    if (z) {
      zSum = `${z.pushed} of ${z.total} in Zoho`;
      zoho = `<div class="check-row ${z.simulated ? 'warn' : 'ok'}"><i class="fa-solid ${z.simulated ? 'fa-flask' : 'fa-plug-circle-check'}"></i>
          <div><b>${z.simulated ? 'Simulation mode' : 'Connected to Zoho Books'}</b><div class="muted">${z.simulated ? 'No Zoho credentials are set, so nothing reaches Zoho. Add ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN and ZOHO_ORG_ID, then test.' : 'Customers are matched by GSTIN or name before any new one is created.'}</div></div>
          <button class="ghost" id="zo-test">Test connection</button></div>
        <div class="progress big"><span id="zo-bar" style="width:${z.total ? z.pushed / z.total * 100 : 0}%"></span></div>
        <p id="zo-line" class="muted">${zohoLine(z)}</p>
        <div class="actions-row"><button id="zo-go" ${z.running || z.pushed === z.total ? 'disabled' : ''}><i class="fa-solid fa-cloud-arrow-up"></i> ${z.pushed && z.pushed < z.total ? 'Push the remaining ' + (z.total - z.pushed) : 'Push ' + z.total + ' invoices to Zoho'}</button></div>
        <div id="zo-errs">${zohoErrors(z)}</div>`;
    }

    body.innerHTML = `${stepper(cur)}
      ${card('inputs', 1, 'Check inputs', blockers ? `${blockers} to fix` : warns ? `${warns} worth a look` : 'all good', inputs, cur, !!run)}
      ${card('calc', 2, 'Calculate', run ? `${q.run.invoices} invoices · ${money(q.run.total)}` : 'not run yet', calc, cur, !!run)}
      ${card('review', 3, 'Review', revSum, rev, cur, run && q && run.status !== 'pending_approval' || (run && q && q.counts.open === 0 && run.status === 'pending_approval' && q.counts.total > 0))}
      ${card('lock', 4, 'Lock', lockSum, lock, cur, run && ['approved', 'pushed'].includes(run.status))}
      ${card('zoho', 5, 'Push to Zoho', zSum, zoho, cur, run && (run.status === 'pushed' || (z && z.total > 0 && z.pushed === z.total)))}`;
    bind(body, chk, run, q, z);
  }

  function reviewRow(i, canReview) {
    const open = c.open.has('r' + i.id);
    const delta = i.change_pct == null || i.change_pct === 0 ? '' : `<div class="delta ${i.change_pct >= 0 ? 'up' : 'down'}">${i.change_pct >= 0 ? '▲' : '▼'} ${Math.abs(i.change_pct)}%</div>`;
    return `<tr data-id="${i.id}" class="${i.status}">
      ${canReview ? `<td>${i.status === 'needs_review' ? `<input type="checkbox" class="rv-sel" ${c.sel.has(i.id) ? 'checked' : ''} aria-label="Select ${esc(i.client_name)}" />` : ''}</td>` : ''}
      <td><button class="linklike rv-open"><i class="fa-solid fa-chevron-${open ? 'down' : 'right'}"></i> <b>${esc(i.client_name)}</b></button><div class="muted mono">${esc(i.invoice_no)}</div></td>
      <td class="right amount">${money(i.amount)}${delta}</td>
      <td class="right amount">${i.prev_amount == null ? '<span class="muted">new</span>' : money(i.prev_amount)}</td>
      <td>${i.reasons.map(r => `<span class="rchip ${r.severity}" title="${esc(r.label)}">${esc(r.label.length > 46 ? r.label.slice(0, 44) + '…' : r.label)}</span>`).join(' ')}
        ${i.status !== 'needs_review' ? `<div class="muted">${i.status === 'excluded' ? 'Left out' : 'Checked'} by ${esc(i.reviewed_by)}${i.comment ? ' — ' + esc(i.comment) : ''}</div>` : ''}</td>
      <td class="right nowrap">${canReview ? (i.status === 'needs_review'
        ? '<button class="ghost rv-ok">Looks right</button> <button class="ghost rv-out" title="Leave this invoice out of the run">Leave out</button>'
        : '<button class="ghost rv-reopen">Reopen</button>') : ''}</td></tr>
      ${open ? `<tr class="rv-detail"><td colspan="6"><div class="table-wrap"><table><tbody>${i.lines.map(l => `<tr><td>${esc(l.label)}</td><td class="muted">${esc(l.detail)}</td><td class="right amount">${money(l.amount)}</td></tr>`).join('')}</tbody></table></div>
        ${i.reasons.map(r => `<div class="muted">• ${esc(r.label)}</div>`).join('')}</td></tr>` : ''}`;
  }

  function bind(body, chk, run, q, z) {
    body.querySelectorAll('details.step-card').forEach(d => d.addEventListener('toggle', () => { d.open ? c.open.add(d.dataset.step) : c.open.delete(d.dataset.step); }));
    const on = (sel, fn) => body.querySelectorAll(sel).forEach(el => el.addEventListener('click', fn));
    const act = async (fn, okMsg) => { try { await fn(); if (okMsg) toast(okMsg); await load(true); } catch (e) { toast(e.message, true); } };

    const go = body.querySelector('#calc-go');
    if (go) go.addEventListener('click', () => { go.disabled = true; go.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Calculating…';
      const fd = new FormData(); fd.append('period', c.period); fd.append('tds_rate', '0');
      act(() => Apx.calculateBillingDb(fd), 'Calculated — review what needs a second look'); });

    on('.seg-btn', (e) => { c.tab = e.currentTarget.dataset.tab; c.sel.clear(); draw(chk, run, q, z); });
    const rs = body.querySelector('#rv-reason'); if (rs) rs.addEventListener('change', (e) => { c.reason = e.target.value; c.sel.clear(); draw(chk, run, q, z); });
    body.querySelectorAll('.rv-sel').forEach(el => el.addEventListener('change', (e) => { const id = Number(e.target.closest('tr').dataset.id); e.target.checked ? c.sel.add(id) : c.sel.delete(id); draw(chk, run, q, z); }));
    on('.rv-open', (e) => { const key = 'r' + e.currentTarget.closest('tr').dataset.id; c.open.has(key) ? c.open.delete(key) : c.open.add(key); draw(chk, run, q, z); });
    const rowId = (e) => Number(e.currentTarget.closest('tr').dataset.id);
    on('.rv-ok', (e) => act(() => Apx.closeApi(`/runs/${run.id}/review/${rowId(e)}`, { method: 'POST', body: { action: 'ok' } })));
    on('.rv-reopen', (e) => act(() => Apx.closeApi(`/runs/${run.id}/review/${rowId(e)}`, { method: 'POST', body: { action: 'reopen' } })));
    on('.rv-out', (e) => { const id = rowId(e); const why = prompt('Why is this invoice being left out of the run?', ''); if (why === null) return;
      act(() => Apx.closeApi(`/runs/${run.id}/review/${id}`, { method: 'POST', body: { action: 'exclude', comment: why } })); });
    const bulk = body.querySelector('#rv-bulk');
    if (bulk) bulk.addEventListener('click', () => {
      const ids = c.sel.size ? [...c.sel] : q.items.filter(i => i.status === 'needs_review' && (c.tab === 'spot' ? i.kind === 'spot' : i.kind === 'flag') && (!c.reason || i.reasons.some(r => (r.code.startsWith('exception:') ? 'exception' : r.code) === c.reason))).map(i => i.id);
      if (!ids.length) return;
      if (ids.length > 1 && !c.sel.size && !confirm(`Mark all ${ids.length} shown invoices as checked?`)) return;
      c.sel.clear(); act(() => Apx.closeApi(`/runs/${run.id}/review-bulk`, { method: 'POST', body: { item_ids: ids } }), `${ids.length} marked as checked`); });

    const show = body.querySelector('#lk-show'); if (show) show.addEventListener('click', () => { const l = body.querySelector('#lk-list'); l.hidden = !l.hidden; });
    const lk = body.querySelector('#lk-go');
    if (lk) lk.addEventListener('click', () => { const ack = body.querySelector('#lk-ack');
      if (ack && !ack.checked) return toast('Tick the box to confirm you read the high-severity findings', true);
      act(() => Apx.approveRun(run.id, !!(ack && ack.checked)), `${c.period} is locked`); });
    const rj = body.querySelector('#lk-reject');
    if (rj) rj.addEventListener('click', () => { const why = prompt('What needs to change before this run can be locked?', ''); if (!why) return;
      act(() => Apx.rejectRun(run.id, why), 'Run sent back'); });

    const t = body.querySelector('#zo-test');
    if (t) t.addEventListener('click', async () => { t.disabled = true; try { const r = await Apx.closeApi('/zoho/test', { method: 'POST' }); toast(r.detail, !r.ok && r.configured); } catch (e) { toast(e.message, true); } t.disabled = false; });
    const zg = body.querySelector('#zo-go');
    if (zg) zg.addEventListener('click', () => act(() => Apx.closeApi(`/runs/${run.id}/zoho/start`, { method: 'POST' })));
  }

  const zohoLine = (z) => z.running ? `Pushing… ${z.pushed} of ${z.total} done. You can leave this page; it keeps going.`
    : z.pushed === z.total && z.total ? `All ${z.total} invoices are in Zoho${z.simulated ? ' (simulated)' : ' as drafts for Zoho to send'}.`
    : z.failed ? `${z.pushed} pushed, ${z.failed} failed — fix the cause below and push again; only the missing ones are sent.` : `${z.total - z.pushed} invoices waiting.`;
  const zohoErrors = (z) => z.errors.length ? `<ul class="plain err">${z.errors.map(e => `<li><b>${esc(e.invoice_no)}</b> — ${esc(e.reason)}</li>`).join('')}</ul>` : '';
  function paintZohoProgress(z) {
    const bar = content.querySelector('#zo-bar'), line = content.querySelector('#zo-line');
    if (bar) bar.style.width = `${z.total ? z.pushed / z.total * 100 : 0}%`;
    if (line) line.textContent = zohoLine(z);
  }

  content.innerHTML = `
    <div class="card close-head"><div><label for="close-period">Billing month</label>
      <input type="month" id="close-period" value="${esc(c.period)}" /></div>
      <p class="muted">Work top to bottom. Each step unlocks the next, and nothing reaches Zoho until a second person has checked and locked the month.</p></div>
    <div id="close-body"></div>`;
  content.querySelector('#close-period').addEventListener('change', (e) => { if (e.target.value) { c.period = e.target.value; c.sel.clear(); c.open.clear(); load(); } });
  await load();
}

function closeReasonName(code) {
  return ({ first_invoice: 'First invoice', partial_month: 'Part-month', minimum_applied: 'Minimum applied', cap_applied: 'Cap applied', swing_up: 'Up sharply', swing_down: 'Down sharply',
    manual_input: 'Hand-entered usage', group_applied: 'Group fee', new_contract: 'New contract', subscription_covered: 'Subscription cover', exception: 'Open finding', spot_check: 'Spot-check' })[code] || code;
}
const canBill = () => state.user && ['admin', 'accounts', 'finance'].includes(state.user.role);

const CLOSE_RENDERERS = { close: renderClose };
