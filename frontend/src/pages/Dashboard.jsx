import React, { useState } from "react";
import { api, count, money } from "../api.js";

export default function Dashboard({ summary, loading, onRefresh, onOpenRule, onBack }) {
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState(null);

  if (loading && !summary) return <div className="empty">Loading today's lists…</div>;
  if (!summary) return null;

  const { run, totals, by_rule, top_salesmen, top_customers } = summary;
  const worst = Math.max(...by_rule.map((r) => Number(r.exposure) || 0), 1);

  const rerun = async () => {
    setRunning(true);
    setMsg(null);
    try {
      const res = await api.createRun({});
      setMsg(`Re-checked ${count(res.scope_rows)} documents.`);
      await onRefresh();
    } catch (err) {
      setMsg(err.message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      {onBack && (
        <button className="back-link" onClick={onBack}>
          <span className="back-arrow">&#8592;</span> Back to Uploads
        </button>
      )}
      <div className="topbar">
        <h1>Outstanding as on {run.snapshot_date}</h1>
        <span className="meta">
          {run.source_file} · {count(run.scope_rows)} documents in scope after filters
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="primary" onClick={rerun} disabled={running}>
          {running ? "Checking…" : "Re-check now"}
        </button>
      </div>
      {msg && <p className="note">{msg}</p>}

      <div className="grid k4" style={{ marginBottom: 18 }}>
        <div className="card stat risk">
          <div className="label">Money at risk on flagged bills</div>
          <div className="value">{money(totals.exposure)}</div>
        </div>
        <div className="card stat">
          <div className="label">Flags raised</div>
          <div className="value">{count(totals.violations)}</div>
        </div>
        <div className="card stat">
          <div className="label">Outlets involved</div>
          <div className="value">{count(totals.customers)}</div>
        </div>
        <div className="card stat">
          <div className="label">Salesmen involved</div>
          <div className="value">{count(totals.salesmen)}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18, padding: 0 }}>
        <div style={{ padding: "16px 16px 0" }}>
          <h2 style={{ marginBottom: 10 }}>Rules</h2>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th>For</th>
                <th className="num">Flags</th>
                <th className="num">Outlets</th>
                <th className="num">At risk</th>
                <th style={{ width: 140 }}>Share of risk</th>
              </tr>
            </thead>
            <tbody>
              {by_rule.map((r) => {
                const disabled = r.is_enabled === false;
                return (
                  <tr
                    key={r.rule_code}
                    className={`row-click${disabled ? " row-disabled" : ""}`}
                    tabIndex={0}
                    onClick={() => onOpenRule(r.rule_code)}
                    onKeyDown={(e) => e.key === "Enter" && onOpenRule(r.rule_code)}
                  >
                    <td>
                      <strong>{r.title}</strong>
                      {disabled && <span className="pill skipped" style={{ marginLeft: 8 }}>off</span>}
                      {r.status === "skipped" && !disabled && (
                        <div className="note">Not run: {r.message}</div>
                      )}
                    </td>
                    <td>{r.audience}</td>
                    <td className="num">{r.status === "skipped" ? "—" : count(r.hits)}</td>
                    <td className="num">{count(r.customers)}</td>
                    <td className="num">{money(r.exposure)}</td>
                    <td>
                      <div className="bar">
                        <span style={{ width: `${(100 * (Number(r.exposure) || 0)) / worst}%` }} />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid k2">
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Salesmen carrying the most risk</h2>
          <table>
            <thead><tr><th>Salesman</th><th>Type</th><th className="num">Flags</th><th className="num">At risk</th></tr></thead>
            <tbody>
              {top_salesmen.map((s) => (
                <tr key={s.salesman_name}>
                  <td>{s.salesman_name}</td>
                  <td className="note">{s.salesman_type}</td>
                  <td className="num">{count(s.flags)}</td>
                  <td className="num">{money(s.exposure)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2 style={{ marginBottom: 10 }}>Outlets carrying the most risk</h2>
          <table>
            <thead><tr><th>Outlet</th><th>Channel</th><th className="num">Flags</th><th className="num">At risk</th></tr></thead>
            <tbody>
              {top_customers.map((c) => (
                <tr key={c.customer_code}>
                  <td>{c.customer_name}<div className="note">{c.customer_code}</div></td>
                  <td className="note">{c.channel_type}</td>
                  <td className="num">{count(c.flags)}</td>
                  <td className="num">{money(c.exposure)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}