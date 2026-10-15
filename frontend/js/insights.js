// ---------------------------------------------------------------------------
// Insights: revenue trend, mix, concentration, movers, take rate, floors, risk, collections, cohorts, forecast
// ---------------------------------------------------------------------------
const insMoney = (n) => n === null || n === undefined ? '—' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const insCompact = (n) => { n = Number(n || 0); const a = Math.abs(n); return (n < 0 ? '−' : '') + '₹' + (a >= 1e7 ? (a / 1e7).toFixed(2) + ' Cr' : a >= 1e5 ? (a / 1e5).toFixed(2) + ' L' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'k' : a.toFixed(0)); };
const insPct = (n, plus) => n === null || n === undefined ? '—' : `${plus && n > 0 ? '+' : ''}${n}%`;
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const delta = (n) => n === null || n === undefined ? '<span class="muted">—</span>' : `<span class="delta ${n >= 0 ? 'up' : 'down'}"><i class="fa-solid fa-arrow-${n >= 0 ? 'up' : 'down'}"></i> ${Math.abs(n)}%</span>`;

function barChart(rows, { valueKey = 'taxable', labelKey = 'period', fmt = insCompact, height = 190, highlight } = {}) {
  const max = Math.max(1, ...rows.map(r => r[valueKey] || 0));
  return `<div class="ins-bars" style="height:${height}px">${rows.map(r => {
    const v = r[valueKey] || 0, h = Math.max(v ? 3 : 1, v / max * 100);
    const label = String(r[labelKey]).length === 7 ? new Date(r[labelKey] + '-01').toLocaleDateString(undefined, { month: 'short' }) : r[labelKey];
    return `<div class="ins-col ${r[labelKey] === highlight ? 'hl' : ''}" title="${esc(String(r[labelKey]))}: ${fmt(v)}"><div class="ins-val">${v ? fmt(v) : ''}</div><div class="ins-bar" style="height:${h}%"></div><div class="ins-lbl">${esc(label)}</div></div>`;
  }).join('')}</div>`;
}

function stackBar(rows) {
  const colors = ['var(--primary)', 'var(--ok)', 'var(--info)', 'var(--warn)', 'var(--danger)', '#a78bfa'];
  return `<div class="stack">${rows.map((r, i) => `<span style="width:${Math.max(r.share, 0)}%;background:${colors[i % colors.length]}" title="${esc(r.label)} ${r.share}%"></span>`).join('')}</div>
    <div class="legend">${rows.map((r, i) => `<div><i style="background:${colors[i % colors.length]}"></i><span>${esc(r.label)}</span><b>${insCompact(r.amount)}</b><em>${r.share}%</em></div>`).join('')}</div>`;
}

function hbars(rows, labelKey, valueKey, shareKey) {
  const max = Math.max(1, ...rows.map(r => r[valueKey]));
  return rows.map(r => `<div class="hbar"><span class="hb-l" title="${esc(r[labelKey])}">${esc(r[labelKey])}</span><div class="hb-t"><div style="width:${r[valueKey] / max * 100}%"></div></div><b>${insCompact(r[valueKey])}</b>${shareKey ? `<em>${r[shareKey]}%</em>` : ''}</div>`).join('') || '<p class="muted">No data.</p>';
}

async function renderInsights(content) {
  const period = state.insPeriod || '';
  const d = await request(`/api/insights/overview${period ? '?period=' + period : ''}`);
  const k = d.kpis, m = d.movers, c = d.collections, f = d.forecast;
  const periods = d.periods.length ? d.periods : [k.period];
  const insight = [];
  if (k.mom_pct !== null && Math.abs(k.mom_pct) >= 10) insight.push(`Revenue ${k.mom_pct > 0 ? 'grew' : 'fell'} <b>${Math.abs(k.mom_pct)}%</b> vs last month${m.new_total ? ` — new customers added ${insCompact(m.new_total)}` : ''}${m.lost_total ? `, churned customers took away ${insCompact(m.lost_total)}` : ''}.`);
  if (d.concentration.top1_share >= 20) insight.push(`<b>${d.concentration.top1_share}%</b> of revenue comes from one customer — concentration risk.`);
  if (d.floors.accounts) insight.push(`<b>${insCompact(d.floors.topup_total)}</b> (${d.floors.share_of_revenue}% of revenue) is minimum-commitment top-ups across ${plural(d.floors.accounts, 'account')}${d.floors.stuck_at_minimum.length ? `; ${d.floors.stuck_at_minimum.length} ${d.floors.stuck_at_minimum.length === 1 ? 'has' : 'have'} sat at their minimum for 3+ months — review pricing or engagement` : ''}.`);
  if (d.risk.declining.length) insight.push(`<b>${d.risk.declining.length}</b> ${d.risk.declining.length === 1 ? 'customer is' : 'customers are'} billing 30%+ below their 3-month average (${insCompact(d.risk.declining_revenue_at_stake)} at stake).`);
  if (c.overdue_pct >= 25) insight.push(`<b>${c.overdue_pct}%</b> of receivables are overdue (${insCompact(c.overdue)}).`);
  if (d.leakage.unmapped_accounts) insight.push(`<b>${d.leakage.unmapped_accounts}</b> ${d.leakage.unmapped_accounts === 1 ? 'account still has' : 'accounts still have'} no enterprise-ID mapping and cannot be billed.`);
  if (f.renewals_count) insight.push(`<b>${f.renewals_count}</b> subscription ${f.renewals_count === 1 ? 'renewal' : 'renewals'} worth ~${insCompact(f.renewals_due_90d)} fall due in the next 90 days.`);
  content.innerHTML = `
    <div class="actions-row"><label class="inline-check">Period <select id="ins-p">${periods.map(p => `<option ${p === k.period ? 'selected' : ''}>${p}</option>`).join('')}</select></label>
      <button class="ghost" id="ins-csv"><i class="fa-solid fa-file-csv"></i> Export trend CSV</button></div>
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-indian-rupee-sign"></i></div><div class="value">${insCompact(k.revenue)}</div><div class="label">Revenue (ex-GST) · ${delta(k.mom_pct)} vs prev</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-chart-line"></i></div><div class="value">${insCompact(k.arr_run_rate)}</div><div class="label">ARR run-rate</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-building"></i></div><div class="value">${k.customers}</div><div class="label">Billed customers · avg ${insCompact(k.avg_per_customer)}</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-rotate"></i></div><div class="value">${insPct(m.nrr_pct)}</div><div class="label">Net revenue retention · churn ${insPct(m.logo_churn_pct)}</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-hourglass-half"></i></div><div class="value">${c.dso_days ?? '—'}${c.dso_days ? ' d' : ''}</div><div class="label">DSO · ${insPct(c.on_time_pct)} paid on time</div></div></div>
    ${insight.length ? `<div class="card ins-callouts"><h3><i class="fa-solid fa-lightbulb"></i> What stands out</h3>${insight.map(t => `<div class="callout">${t}</div>`).join('')}</div>` : ''}
    <div class="grid-2">
      <div class="card"><h3>Revenue trend</h3>${barChart(d.trend, { highlight: k.period })}<p class="muted" style="margin:.4rem 0 0">Trailing 12 months: <b>${insCompact(k.ttm_revenue)}</b> · next-month estimate <b>${insCompact(f.next_month_estimate)}</b></p></div>
      <div class="card"><h3>Revenue mix by charge type</h3>${d.mix.length ? stackBar(d.mix) : '<p class="muted">No invoices this month.</p>'}</div>
    </div>
    <div class="grid-2">
      <div class="card"><h3>Customer concentration</h3><p class="muted">Top 10 = <b>${d.concentration.top10_share}%</b> · largest = ${d.concentration.top1_share}% · HHI ${d.concentration.hhi} · median bill ${insCompact(d.concentration.median_bill)}</p>${hbars(d.concentration.top, 'customer', 'amount', 'share')}</div>
      <div class="card"><h3>Movers vs last month</h3>
        <div class="mover-stats"><div><b class="up">+${insCompact(m.expansion)}</b><span>expansion</span></div><div><b class="down">${insCompact(m.contraction)}</b><span>contraction</span></div><div><b class="up">+${insCompact(m.new_total)}</b><span>new</span></div><div><b class="down">−${insCompact(m.lost_total)}</b><span>lost</span></div></div>
        ${tbl(['Customer', '>Change', '>Now'], [...m.up.slice(0, 4).map(r => `<tr><td>${esc(r.customer)}</td><td class="right">${delta(r.pct)} <span class="amount up">+${insCompact(r.delta)}</span></td><td class="right amount">${insCompact(r.now)}</td></tr>`), ...m.down.slice(0, 4).map(r => `<tr><td>${esc(r.customer)}</td><td class="right">${delta(r.pct)} <span class="amount down">${insCompact(r.delta)}</span></td><td class="right amount">${insCompact(r.now)}</td></tr>`)], 'No month-on-month movement yet.')}
        ${m.new.length ? `<p class="muted" style="margin:.5rem 0 0">New: ${m.new.slice(0, 4).map(r => esc(r.customer)).join(', ')}</p>` : ''}${m.lost.length ? `<p class="muted" style="margin:.2rem 0 0">Lost: ${m.lost.slice(0, 4).map(r => esc(r.customer)).join(', ')}</p>` : ''}</div>
    </div>
    <div class="grid-2">
      <div class="card"><h3>Take rate &amp; usage</h3>${tbl(['Month', '>Add Money', '>UPI txns', '>Makers', '>Take rate', '>₹ / UPI txn'], d.usage.filter(u => u.add_money !== null).slice(-6).map(u => `<tr><td>${esc(u.period)}</td><td class="right amount">${insCompact(u.add_money)}</td><td class="right">${(u.upi_txns || 0).toLocaleString('en-IN')}</td><td class="right">${u.makers ?? '—'}</td><td class="right">${u.take_rate_pct === null ? '—' : u.take_rate_pct + '%'}</td><td class="right amount">${u.revenue_per_upi_txn === null ? '—' : '₹' + u.revenue_per_upi_txn}</td></tr>`), 'Usage snapshots appear after the first run.')}<p class="muted" style="margin:.4rem 0 0">Take rate = revenue ÷ funds loaded: how much you earn per ₹100 that flows through the platform.</p></div>
      <div class="card"><h3>Minimum-commitment revenue</h3><p class="muted"><b>${insCompact(d.floors.topup_total)}</b> this month from ${plural(d.floors.accounts, 'account')} billed at their minimum.</p>${hbars(d.floors.top, 'customer', 'topup')}
        ${d.floors.stuck_at_minimum.length ? `<h4>At minimum for 3+ months</h4><p>${d.floors.stuck_at_minimum.map(r => `<span class="badge draft">${esc(r.customer)}</span>`).join(' ')}</p>` : ''}</div>
    </div>
    <div class="grid-2">
      <div class="card"><h3>Declining customers</h3>${tbl(['Customer', '>Now', '>3-mo avg', '>Drop'], d.risk.declining.map(r => `<tr><td>${esc(r.customer)}</td><td class="right amount">${insCompact(r.now)}</td><td class="right amount">${insCompact(r.avg_prior_3m)}</td><td class="right"><span class="delta down">${r.drop_pct}%</span></td></tr>`), 'No customers are billing materially below their recent average.')}</div>
      <div class="card"><h3>Collections</h3><div class="mover-stats"><div><b>${insCompact(c.outstanding)}</b><span>outstanding</span></div><div><b class="${c.overdue_pct >= 25 ? 'down' : ''}">${insCompact(c.overdue)}</b><span>overdue (${c.overdue_pct}%)</span></div><div><b>${c.avg_days_to_pay_vs_due === null ? '—' : (c.avg_days_to_pay_vs_due > 0 ? '+' : '') + c.avg_days_to_pay_vs_due + ' d'}</b><span>vs due date</span></div></div>
        <h4>Data health</h4>${d.leakage.by_code.length ? d.leakage.by_code.map(x => `<div class="hbar"><span class="hb-l mono" style="font-size:.74rem">${esc(x.code)}</span><div class="hb-t"><div style="width:${x.count / d.leakage.by_code[0].count * 100}%;background:var(--warn)"></div></div><b>${x.count}</b></div>`).join('') : '<p class="muted">No open exceptions.</p>'}</div>
    </div>
    <div class="card"><h3>Customers by go-live year</h3>${tbl(['Cohort', '>Accounts', '>Revenue this month', '>Share'], d.cohorts.map(r => `<tr><td>${esc(r.cohort)}</td><td class="right">${r.accounts}</td><td class="right amount">${insCompact(r.revenue)}</td><td class="right">${r.share}%</td></tr>`), 'No billing accounts yet.')}</div>`;
  document.getElementById('ins-p').addEventListener('change', e => { state.insPeriod = e.target.value; navigate('insights'); });
  document.getElementById('ins-csv').addEventListener('click', () => csvDownload('revenue_trend.csv', [['Period', 'Taxable', 'GST', 'Invoices', 'Customers'], ...d.trend.map(t => [t.period, t.taxable, t.gst, t.invoices, t.customers])]));
}

async function v2RenewalsTab(panel) {
  const horizon = state.renHorizon || 90;
  const r = await Apx.v2(`/renewals?horizon_days=${horizon}`);
  panel.innerHTML = `<div class="stat-grid"><div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-triangle-exclamation"></i></div><div class="value">${insCompact(r.overdue_value)}</div><div class="label">Overdue renewals</div></div>
      <div class="stat-card"><div class="stat-ico"><i class="fa-solid fa-calendar-check"></i></div><div class="value">${insCompact(r.due_value)}</div><div class="label">Due in the next ${horizon} days</div></div></div>
    <div class="card"><div class="actions-row"><label class="inline-check">Look ahead <select id="rn-h">${[30, 60, 90, 180, 365].map(h => `<option ${h === horizon ? 'selected' : ''}>${h}</option>`).join('')}</select> days</label></div>
      ${tbl(['Account', 'Frequency', 'Ended / ends', '>Days', '>Last amount', '>Expected', 'Next period', ''], r.rows.map(x => `<tr data-id="${x.account_id}"><td><b>${esc(x.account)}</b></td><td>${esc(x.frequency)}</td><td>${esc(x.last_period_end)}</td>
        <td class="right"><span class="badge ${x.status === 'overdue' ? 'error' : 'draft'}">${x.status === 'overdue' ? Math.abs(x.days_left) + ' overdue' : x.days_left + ' left'}</span></td><td class="right amount">${insMoney(x.last_amount)}</td><td class="right amount">${insMoney(x.expected_amount)}</td><td class="muted">${esc(x.next_start)} → ${esc(x.next_end)}</td>
        <td>${isFinance() ? `<button class="ren-raise" data-amt="${x.expected_amount}">Raise renewal</button>` : ''}</td></tr>`), 'No renewals due in this window.')}
      <p class="muted" style="margin:.6rem 0 0">Raising a renewal records the new subscription period (so the monthly run stops billing the platform fee for it) and issues the invoice into your books and GST report.</p></div>`;
  panel.querySelector('#rn-h').onchange = e => { state.renHorizon = Number(e.target.value); v2RenewalsTab(panel); };
  panel.querySelectorAll('.ren-raise').forEach(b => b.onclick = async e => {
    const tr = e.target.closest('tr'); const amt = prompt('Renewal amount (ex-GST) ₹', b.dataset.amt); if (amt === null) return;
    try { const res = await Apx.v2(`/renewals/${tr.dataset.id}/raise`, { method: 'POST', body: { amount: Number(amt), invoice: true } }); toast(`Renewal raised: ${res.period_start} → ${res.period_end}${res.invoice_no ? ' · ' + res.invoice_no : ''}`); v2RenewalsTab(panel); } catch (er) { toast(er.message, true); } });
}

const INSIGHT_RENDERERS = { insights: renderInsights };
