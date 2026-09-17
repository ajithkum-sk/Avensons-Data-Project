const BASE = import.meta.env.VITE_API_BASE ?? "";

async function call(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  dashboard: (runId) => call(`/api/dashboard${runId ? `?run_id=${runId}` : ""}`),
  violations: (params) => call(`/api/violations?${new URLSearchParams(params)}`),
  csvUrl: (params) => `${BASE}/api/violations.csv?${new URLSearchParams(params)}`,
  customer: (code) => call(`/api/customers/${encodeURIComponent(code)}`),
  salesmen: () => call("/api/salesmen"),
  rules: () => call("/api/rules"),
  patchRule: (code, body) =>
    call(`/api/rules/${code}`, { method: "PATCH", body: JSON.stringify(body) }),
  config: () => call("/api/config"),
  putConfig: (params) =>
    call("/api/config", { method: "PUT", body: JSON.stringify({ params }) }),
  snapshots: () => call("/api/snapshots"),
  runs: () => call("/api/runs"),
  createRun: (body = {}) =>
    call("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  exceptions: () => call("/api/exceptions"),
  addException: (body) =>
    call("/api/exceptions", { method: "POST", body: JSON.stringify(body) }),
  dropException: (id) => call(`/api/exceptions/${id}`, { method: "DELETE" }),
  addNote: (violationId, body) =>
    call(`/api/violations/${violationId}/notes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  upload: async (file, { snapshotType = "daily", snapshotDate = "" } = {}) => {
    const form = new FormData();
    form.append("file", file);
    form.append("snapshot_type", snapshotType);
    if (snapshotDate) form.append("snapshot_date", snapshotDate);
    const res = await fetch(`${BASE}/api/snapshots/upload`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Upload failed");
    }
    return res.json();
  },
};

export const money = (n) =>
  n == null ? "—" : `\u20B9${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

export const count = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
