const API_BASE = window.location.protocol + '//' + window.location.hostname + ':8502';

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
  devLogin: (email, role) => request('/auth/dev-login', { method: 'POST', body: { email, role } }),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  providers: () => request('/auth/providers'),
  health: () => request('/health'),

  // clients
  listClients: () => request('/api/clients'),
  createClient: (data) => request('/api/clients', { method: 'POST', body: data }),
  updateClient: (id, data) => request(`/api/clients/${id}`, { method: 'PUT', body: data }),
  deleteClient: (id) => request(`/api/clients/${id}`, { method: 'DELETE' }),
  bulkImportClients: (file) => {
    const fd = new FormData(); fd.append('file', file);
    return request('/api/clients/bulk', { method: 'POST', body: fd, isForm: true });
  },
  clientTemplateUrl: () => `${API_BASE}/api/clients/template`,

  // commercials
  listCommercials: () => request('/api/commercials'),
  saveCommercial: (data) => request('/api/commercials', { method: 'POST', body: data }),
  lockCommercial: (id) => request(`/api/commercials/${id}/lock`, { method: 'POST' }),
  unlockCommercial: (id) => request(`/api/commercials/${id}/unlock`, { method: 'POST' }),
  commercialVersions: (id) => request(`/api/commercials/${id}/versions`),
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
  calculateBilling: (formData) => request('/api/billing/calculate', { method: 'POST', body: formData, isForm: true }),
  listRuns: () => request('/api/billing/runs'),
  runDetail: (id) => request(`/api/billing/runs/${id}`),
  pushZoho: (id) => request(`/api/billing/runs/${id}/push-zoho`, { method: 'POST' }),
  listInvoices: () => request('/api/billing/invoices'),
  expiryAlerts: () => request('/api/billing/alerts/expiry'),
  annualDueAlerts: (period) => request(`/api/billing/alerts/annual-due?period=${period}`),
  createZohoContact: (clientId) => request(`/api/billing/zoho/create-contact/${clientId}`, { method: 'POST' }),
  reportExportUrl: (kind) => `${API_BASE}/api/billing/reports/export?kind=${kind}`,
};
