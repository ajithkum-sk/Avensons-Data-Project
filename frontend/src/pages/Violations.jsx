import React, { useCallback, useEffect, useState } from "react";
import { api, count, money } from "../api.js";
import CustomerDrawer from "../components/CustomerDrawer.jsx";

const PAGE = 100;

export default function Violations({ ruleCode, rule, onBack }) {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(null);
  const [openCustomer, setOpenCustomer] = useState(null);

  const params = { rule_code: ruleCode, limit: PAGE, offset };
  if (search.trim()) params.search = search.trim();

  const load = useCallback(async () => {
    try {
      setData(await api.violations(params));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [ruleCode, search, offset]);

  useEffect(() => {
    const timer = setTimeout(load, search ? 250 : 0); // debounce typing
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => setOffset(0), [ruleCode, search]);

  return (
    <>
      {onBack && (
        <button className="back-link" onClick={onBack}>
          <span className="back-arrow">&#8592;</span> Back to Dashboard
        </button>
      )}
      <div className="topbar">
        <h1>{rule?.title ?? ruleCode}</h1>
        {rule?.purpose && <span className="meta">{rule.purpose}</span>}
      </div>

      {rule?.data_note && <div className="banner" style={{ marginBottom: 14 }}>{rule.data_note}</div>}
      {error && <div className="error">{error}</div>}

      <div className="toolbar">
        <input
          type="search"
          placeholder="Outlet, salesman or bill number"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 300 }}
          aria-label="Search this list"
        />
        <span className="spacer" />
        {data && (
          <span className="note">
            {count(data.total)} flags · {money(data.exposure)} at risk
          </span>
        )}
        <button type="button" className="ghost print-btn" onClick={() => window.print()} title="Print or save as PDF">
          <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M5.5 7.5V3.5h9v4" strokeLinecap="round" strokeLinejoin="round" />
            <rect x="3" y="7.5" width="14" height="6.5" rx="1.2" />
            <path d="M5.5 12.5v4h9v-4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Print
        </button>
        <a className="export-btn" href={api.csvUrl({ rule_code: ruleCode, ...(search ? { search } : {}) })}>
          Export to Excel (CSV)
        </a>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div className="table-scroll">
          <table className="violations-table">
            <thead>
              <tr>
                <th>Outlet</th>
                <th>Salesman</th>
                <th>Bill</th>
                <th className="num">Amount</th>
                <th className="num">Balance</th>
                <th className="num">Overdue</th>
                <th>Why it is flagged</th>
              </tr>
            </thead>
            <tbody>
              {data?.rows.map((r) => (
                <tr key={r.violation_id} className="row-click" tabIndex={0}
                    onClick={() => setOpenCustomer(r.customer_code)}
                    onKeyDown={(e) => e.key === "Enter" && setOpenCustomer(r.customer_code)}>
                  <td>
                    {r.customer_name}
                    <div className="note">{r.customer_code} · {r.channel_type}</div>
                  </td>
                  <td>
                    {r.salesman_name ?? "—"}
                    <div className="note">{r.beat ?? ""}</div>
                  </td>
                  <td>
                    {r.document_no ?? "—"}
                    <div className="note">{r.document_date ?? ""}</div>
                  </td>
                  <td className="num">{money(r.amount)}</td>
                  <td className="num">{money(r.balance)}</td>
                  <td className="num">{r.overdue_days ?? "—"}</td>
                  <td>{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.rows.length === 0 && (
          <div className="empty">Nothing flagged here. Either the shops are clean or the threshold needs a look in Rule settings.</div>
        )}
      </div>

      {data && data.total > PAGE && (
        <div className="toolbar" style={{ marginTop: 12 }}>
          <button className="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Previous
          </button>
          <span className="note">
            {offset + 1}–{Math.min(offset + PAGE, data.total)} of {count(data.total)}
          </span>
          <button className="ghost" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
            Next
          </button>
        </div>
      )}

      {openCustomer && (
        <CustomerDrawer customerCode={openCustomer} onClose={() => setOpenCustomer(null)} />
      )}
    </>
  );
}