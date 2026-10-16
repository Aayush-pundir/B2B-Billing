// Frontend is served by the same FastAPI process as the API — same origin,
// same port, no CORS involved. Relative paths only.
const API_BASE = '';

async function request(path, { method = 'GET', body, isForm = false } = {}) {
  const opts = { method, credentials: 'include', headers: {} };
  if (body !== undefined) {
    if (isForm) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(API_BASE + path, opts);
  if (res.status === 401) return null;
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return res;
}

const Apx = {
  // auth
  devLogin: (email, role, passcode = '') => request('/auth/dev-login', { method: 'POST', body: { email, role, passcode } }),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  providers: () => request('/auth/providers'),
  health: () => request('/health'),

  // clients
  listClients: () => request('/api/clients'),
  createClient: (data) => request('/api/clients', { method: 'POST', body: data }),
  updateClient: (id, data) => request(`/api/clients/${id}`, { method: 'PUT', body: data }),
  deleteClient: (id) => request(`/api/clients/${id}`, { method: 'DELETE' }),
  bulkImportClients: (file, updateExisting = false) => {
    const fd = new FormData(); fd.append('file', file);
    return request(`/api/clients/bulk?update_existing=${updateExisting}`, { method: 'POST', body: fd, isForm: true });
  },
  clientTemplateUrl: () => `${API_BASE}/api/clients/template`,
  clientExportUrl: () => `${API_BASE}/api/clients/export`,

  // commercials
  listCommercials: () => request('/api/commercials'),
  saveCommercial: (data) => request('/api/commercials', { method: 'POST', body: data }),
  lockCommercial: (id) => request(`/api/commercials/${id}/lock`, { method: 'POST' }),
  unlockCommercial: (id) => request(`/api/commercials/${id}/unlock`, { method: 'POST' }),
  commercialVersions: (id) => request(`/api/commercials/${id}/versions`),
  termsExcelTemplateUrl: () => `${API_BASE}/api/commercials/excel-template`,
  bulkExcelTerms: (file, dryRun) => {
    const fd = new FormData(); fd.append('file', file);
    return request(`/api/commercials/bulk-excel?dry_run=${dryRun}`, { method: 'POST', body: fd, isForm: true });
  },
  importAgreementTerms: (agreementId) => request(`/api/commercials/from-agreement/${agreementId}`, { method: 'POST' }),
  escalateCommercial: (id, pct) => request(`/api/commercials/${id}/escalate`, { method: 'POST', body: { pct } }),

  // metrics / addons
  listMetrics: () => request('/api/metrics'),
  createMetric: (data) => request('/api/metrics', { method: 'POST', body: data }),
  listAddons: () => request('/api/addons'),
  createAddon: (data) => request('/api/addons', { method: 'POST', body: data }),

  // users
  listUsers: () => request('/api/users'),
  createUser: (data) => request('/api/users', { method: 'POST', body: data }),
  deleteUser: (id) => request(`/api/users/${id}`, { method: 'DELETE' }),

  // audit
  auditLog: () => request('/api/audit'),

  // templates
  templateUrl: (kind) => `${API_BASE}/api/templates/${kind}`,

  // settings
  getSettings: () => request('/api/settings'),
  putSettings: (values) => request('/api/settings', { method: 'PUT', body: { values } }),

  // agreements
  listAgreements: () => request('/api/agreements'),
  uploadAgreement: (formData) => request('/api/agreements/upload', { method: 'POST', body: formData, isForm: true }),
  bulkUploadAgreements: (formData) => request('/api/agreements/bulk', { method: 'POST', body: formData, isForm: true }),
  extractAgreement: (id) => request(`/api/agreements/${id}/extract`, { method: 'POST' }),

  // billing
  calculateBillingDb: (formData) => request('/api/billing/calculate-db', { method: 'POST', body: formData, isForm: true }),
  datasource: () => request('/api/datasource'),
  saveDatasource: (queries) => request('/api/datasource', { method: 'PUT', body: { queries } }),
  testDatasource: (period) => request('/api/datasource/test', { method: 'POST', body: { period } }),
  calculateBilling: (formData) => request('/api/billing/calculate', { method: 'POST', body: formData, isForm: true }),
  listRuns: () => request('/api/billing/runs'),
  runDetail: (id) => request(`/api/billing/runs/${id}`),
  pushZoho: (id) => request(`/api/billing/runs/${id}/push-zoho`, { method: 'POST' }),
  listInvoices: () => request('/api/billing/invoices'),
  // server-side filter + paging; resolves to { rows, total }
  pageInvoices: async (params) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)).toString();
    const res = await fetch(`${API_BASE}/api/billing/invoices?${qs}`, { credentials: 'include' });
    if (res.status === 401) return null;
    if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch (e) {} throw new Error(d); }
    return { rows: await res.json(), total: Number(res.headers.get('X-Total-Count') || 0) };
  },
  dashboard: () => request('/api/dashboard'),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  expiryAlerts: () => request('/api/billing/alerts/expiry'),
  annualDueAlerts: (period) => request(`/api/billing/alerts/annual-due?period=${period}`),
  createZohoContact: (clientId) => request(`/api/billing/zoho/create-contact/${clientId}`, { method: 'POST' }),
  reportExportUrl: (kind) => `${API_BASE}/api/billing/reports/export?kind=${kind}`,

  // items
  listItems: () => request('/api/items'),
  saveItem: (id, data) => id ? request(`/api/items/${id}`, { method: 'PUT', body: data }) : request('/api/items', { method: 'POST', body: data }),
  deleteItem: (id) => request(`/api/items/${id}`, { method: 'DELETE' }),

  // sales documents
  previewDoc: (clientId, lines) => request('/api/sales/preview', { method: 'POST', body: { client_id: clientId, lines } }),
  createInvoiceDoc: (data) => request('/api/sales/invoices', { method: 'POST', body: data }),
  createDebitNote: (data) => request('/api/sales/debit-notes', { method: 'POST', body: data }),
  listDocs: () => request('/api/sales/documents'),
  writeOff: (id, reason) => request(`/api/sales/invoices/${id}/write-off`, { method: 'POST', body: { reason } }),
  recordReceipt: (data) => request('/api/sales/receipts', { method: 'POST', body: data }),
  listAdvances: () => request('/api/sales/advances'),
  listQuotes: () => request('/api/sales/quotes'),
  createQuote: (data) => request('/api/sales/quotes', { method: 'POST', body: data }),
  setQuoteStatus: (id, status) => request(`/api/sales/quotes/${id}/status`, { method: 'PUT', body: { status } }),
  convertQuote: (id) => request(`/api/sales/quotes/${id}/convert`, { method: 'POST' }),
  quotePdfUrl: (id) => `${API_BASE}/api/sales/quotes/${id}/pdf`,
  listRecurring: () => request('/api/sales/recurring'),
  createRecurring: (data) => request('/api/sales/recurring', { method: 'POST', body: data }),
  setRecurringStatus: (id, status) => request(`/api/sales/recurring/${id}/status`, { method: 'PUT', body: { status } }),
  runRecurring: () => request('/api/sales/recurring/run', { method: 'POST' }),

  // purchases
  listVendors: () => request('/api/purchases/vendors'),
  saveVendor: (id, data) => id ? request(`/api/purchases/vendors/${id}`, { method: 'PUT', body: data }) : request('/api/purchases/vendors', { method: 'POST', body: data }),
  listBills: () => request('/api/purchases/bills'),
  createBill: (data) => request('/api/purchases/bills', { method: 'POST', body: data }),
  voidBill: (id) => request(`/api/purchases/bills/${id}/void`, { method: 'POST' }),
  payBill: (id, data) => request(`/api/purchases/bills/${id}/pay`, { method: 'POST', body: data }),

  // accounting + reports
  listAccounts: () => request('/api/finance/accounts'),
  saveAccount: (id, data) => id ? request(`/api/finance/accounts/${id}`, { method: 'PUT', body: data }) : request('/api/finance/accounts', { method: 'POST', body: data }),
  listJournals: () => request('/api/finance/journals'),
  createJournal: (data) => request('/api/finance/journals', { method: 'POST', body: data }),
  reverseJournal: (id) => request(`/api/finance/journals/${id}/reverse`, { method: 'POST' }),
  getLock: () => request('/api/finance/lock'),
  setLock: (date) => request('/api/finance/lock', { method: 'PUT', body: { date } }),
  ledger: (account, start, end) => request(`/api/finance/ledger?account=${account}&start=${start}&end=${end}`),
  finReport: (path, qs) => request(`/api/finance/reports/${path}${qs ? '?' + qs : ''}`),
  gstExportUrl: (start, end) => `${API_BASE}/api/finance/reports/gst/export?start=${start}&end=${end}`,
  integrity: () => request('/api/finance/integrity'),

  // billing engine v2
  v2: (path, opts) => request('/api/v2' + path, opts),
  v2form: (path, file, params = '') => { const fd = new FormData(); fd.append('file', file); return request('/api/v2' + path + params, { method: 'POST', body: fd, isForm: true }); },
  v2mappingTemplateUrl: () => `${API_BASE}/api/v2/import/mapping-template`,

  // approvals
  approveRun: (id, acknowledgeHigh) => request(`/api/billing/runs/${id}/approve`, { method: 'POST', body: { acknowledge_high: acknowledgeHigh } }),
  rejectRun: (id, reason) => request(`/api/billing/runs/${id}/reject`, { method: 'POST', body: { reason } }),
  issueAll: (id) => request(`/api/billing/runs/${id}/issue-all`, { method: 'POST' }),

  // automation + credit control
  automation: () => request('/api/automation'),
  saveAutomation: (values) => request('/api/automation', { method: 'PUT', body: { values } }),
  runAutomation: (steps) => request('/api/automation/run-now', { method: 'POST', body: { steps } }),
  creditExposure: () => request('/api/credit/exposure'),
  creditForecast: () => request('/api/credit/forecast'),
  creditEvaluate: () => request('/api/credit/evaluate', { method: 'POST' }),
  setCustomerStatus: (id, status, reason) => request(`/api/credit/customers/${id}/status`, { method: 'PUT', body: { status, reason } }),

  // invoice lifecycle (receivables)
  issueInvoice: (id) => request(`/api/billing/invoices/${id}/issue`, { method: 'POST' }),
  sendInvoice: (id) => request(`/api/billing/invoices/${id}/send`, { method: 'POST' }),
  invoicePdfUrl: (id) => `${API_BASE}/api/billing/invoices/${id}/pdf`,
  disputeInvoice: (id, disputed, reason) => request(`/api/billing/invoices/${id}/dispute`, { method: 'PUT', body: { disputed, reason } }),
  creditNote: (id, amount, reason) => request(`/api/billing/invoices/${id}/credit-note`, { method: 'POST', body: { amount, reason } }),
  clientStatement: (clientId) => request(`/api/clients/${clientId}/statement`),
  runReminders: () => request('/api/billing/reminders/run', { method: 'POST' }),
  remindersLog: () => request('/api/billing/reminders/log'),

  // reconciliation
  agingReport: () => request('/api/billing/receivables/aging'),
  uploadStatement: (formData) => request('/api/billing/reconciliation/upload-statement', { method: 'POST', body: formData, isForm: true }),
  manualCollectionsUpload: (formData) => request('/api/billing/reconciliation/manual-upload', { method: 'POST', body: formData, isForm: true }),
  reconciliationQueue: () => request('/api/billing/reconciliation/queue'),
  confirmMatch: (paymentId, invoiceId, amount) => request(`/api/billing/reconciliation/payments/${paymentId}/confirm`, { method: 'POST', body: { invoice_id: invoiceId, amount } }),
  dismissPayment: (paymentId) => request(`/api/billing/reconciliation/payments/${paymentId}/dismiss`, { method: 'POST' }),

  // CRM sync
  crmSync: (formData, source) => request(`/api/crm/sync?source=${source}`, { method: 'POST', body: formData, isForm: true }),
  crmSyncRuns: () => request('/api/crm/sync-runs'),
  crmTemplateUrl: () => `${API_BASE}/api/crm/template`,
};
