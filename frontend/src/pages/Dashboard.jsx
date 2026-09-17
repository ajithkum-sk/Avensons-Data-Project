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

      <h2 style={{ marginBottom: 10 }}>Rules</h2>
      <div className="rule-cards" style={{ marginBottom: 18 }}>
        {by_rule.map((r) => (
          <button
            key={r.rule_code}
            className={`rule-card${r.status === "skipped" ? " rule-card-skipped" : ""}`}
            onClick={() => onOpenRule(r.rule_code)}
          >
            <div className="rule-card-top">
              <strong>{r.title}</strong>
              <span className={`pill ${r.severity}`}>{r.severity}</span>
            </div>
            <div className="note rule-card-audience">For: {r.audience}</div>

            {r.status === "skipped" ? (
              <div className="note rule-card-skip-msg">Not run: {r.message}</div>
            ) : (
              <>
                <div className="rule-card-stats">
                  <div>
                    <span className="rule-card-num">{count(r.hits)}</span>
                    <span className="rule-card-num-label">flags</span>
                  </div>
                  <div>
                    <span className="rule-card-num">{count(r.customers)}</span>
                    <span className="rule-card-num-label">outlets</span>
                  </div>
                  <div>
                    <span className="rule-card-num rule-card-risk">{money(r.exposure)}</span>
                    <span className="rule-card-num-label">at risk</span>
                  </div>
                </div>
                <div className="bar rule-card-bar">
                  <span style={{ width: `${(100 * (Number(r.exposure) || 0)) / worst}%` }} />
                </div>
              </>
            )}
          </button>
        ))}
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