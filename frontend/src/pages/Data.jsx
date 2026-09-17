import React, { useEffect, useRef, useState } from "react";
import { api, count } from "../api.js";

const LOCATIONS = ["Coimbatore (CBE)", "Chennai", "Madurai", "Salem", "Trichy"];

export default function Data({ onLoaded, onOpenRun }) {
  const [snapshots, setSnapshots] = useState([]);
  const [runs, setRuns] = useState([]);
  const [type, setType] = useState("daily");
  const [asOn, setAsOn] = useState("");
  const [location, setLocation] = useState(LOCATIONS[0]);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const reload = () =>
    Promise.all([api.snapshots(), api.runs()])
      .then(([s, r]) => {
        setSnapshots(s);
        setRuns(r);
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    reload();
  }, []);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.upload(file, { snapshotType: type, snapshotDate: asOn });
      setResult(res);
      await reload();
      onLoaded?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const runForSnapshot = (snapshotDate) => {
    const candidates = runs.filter((r) => r.snapshot_date === snapshotDate);
    const success = candidates.filter((r) => r.status === "success");
    const pool = success.length ? success : candidates;
    return pool.sort((a, b) => b.run_id - a.run_id)[0] ?? null;
  };

  const remove = async (snapshotId, snapshotDate) => {
    if (!window.confirm(`Delete the report loaded for ${snapshotDate}? This also removes its rule checks and cannot be undone.`)) {
      return;
    }
    setDeletingId(snapshotId);
    setError(null);
    try {
      await api.deleteSnapshot(snapshotId);
      await reload();
      onLoaded?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <div className="topbar">
        <h1>Uploads</h1>
        <span className="meta">
          Drop the outstanding report here each evening. Loading the same date twice replaces it.
        </span>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ marginBottom: 18 }}>
        <h2 style={{ marginBottom: 10 }}>Load an outstanding report</h2>
        <div className="toolbar">
          <input type="file" ref={fileRef} accept=".xlsx,.xls,.xlsm" aria-label="Excel file" />
          <select value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Location">
            {LOCATIONS.map((loc) => (
              <option key={loc} value={loc}>{loc}</option>
            ))}
          </select>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Report type">
            <option value="daily">Daily outstanding</option>
            <option value="monthly">Monthly sales</option>
          </select>
          <label className="note">
            As on{" "}
            <input type="date" value={asOn} onChange={(e) => setAsOn(e.target.value)} />
          </label>
          <button className="primary" onClick={upload} disabled={busy}>
            {busy ? "Loading and checking…" : "Load and check"}
          </button>
        </div>
        <p className="note">
          Leave the date blank and SalesGuard reads the print date from the top of the sheet.
          {" "}The location tag isn't sent to the server yet — it's local to this screen for now.
        </p>
        {result && (
          <div className="banner">
            Loaded {count(result.documents)} documents for {result.snapshot_date} —{" "}
            {count(result.customers)} outlets, {count(result.salesmen)} salesmen.{" "}
            {result.run
              ? `${count(result.run.rules.reduce((a, r) => a + r.hits, 0))} flags raised across ${result.run.rules.length} rules.`
              : ""}
          </div>
        )}
      </div>

      <div className="grid k2">
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Loaded reports</h2>
          <table>
            <thead>
              <tr><th>As on</th><th>Type</th><th>File</th><th className="num">Documents</th><th aria-label="Actions" /></tr>
            </thead>
            <tbody>
              {snapshots.map((s) => {
                const run = runForSnapshot(s.snapshot_date);
                return (
                  <tr
                    key={s.snapshot_id}
                    className={run ? "row-click" : ""}
                    tabIndex={run ? 0 : undefined}
                    title={run ? "Open this report's dashboard" : "No rule check has run for this report yet"}
                    onClick={() => run && onOpenRun?.(run.run_id)}
                    onKeyDown={(e) => run && e.key === "Enter" && onOpenRun?.(run.run_id)}
                  >
                    <td>{s.snapshot_date}</td>
                    <td>{s.snapshot_type}</td>
                    <td className="note">{s.source_file}</td>
                    <td className="num">{count(s.documents)}</td>
                    <td className="delete-col">
                      <button
                        className="row-delete-btn"
                        title="Delete this report"
                        aria-label={`Delete report loaded for ${s.snapshot_date}`}
                        disabled={deletingId === s.snapshot_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          remove(s.snapshot_id, s.snapshot_date);
                        }}
                      >
                        {deletingId === s.snapshot_id ? "…" : "\u2715"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {snapshots.length === 0 && <div className="empty">Nothing loaded yet.</div>}
        </div>

        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Rule checks</h2>
          <table>
            <thead>
              <tr><th>Run</th><th>As on</th><th>Status</th><th className="num">In scope</th><th className="num">Flags</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.run_id}
                  className={r.status === "success" ? "row-click" : ""}
                  tabIndex={r.status === "success" ? 0 : undefined}
                  onClick={() => r.status === "success" && onOpenRun?.(r.run_id)}
                  onKeyDown={(e) => r.status === "success" && e.key === "Enter" && onOpenRun?.(r.run_id)}
                >
                  <td>#{r.run_id}</td>
                  <td>{r.snapshot_date}</td>
                  <td>{r.status}</td>
                  <td className="num">{count(r.scope_rows)}</td>
                  <td className="num">{count(r.violations)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {runs.length === 0 && <div className="empty">No checks run yet.</div>}
        </div>
      </div>
    </>
  );
}