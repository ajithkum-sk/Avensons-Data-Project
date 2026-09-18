import React, { useEffect, useState } from "react";
import { api, money } from "../api.js";

export default function CustomerDrawer({ customerCode, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.customer(customerCode).then(setData).catch((e) => setError(e.message));
  }, [customerCode]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label="Outlet detail" onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            <h2>{data?.customer.customer_name ?? customerCode}</h2>
            <div className="note">{customerCode}</div>
          </div>
          <button className="ghost" onClick={onClose}>Close</button>
        </header>

        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="empty">Loading outlet…</div>}

        {data && (
          <>
            <div className="card" style={{ marginBottom: 14 }}>
              <dl className="kv">
                <dt>Channel</dt><dd>{data.customer.channel_type ?? "—"}</dd>
                <dt>Outlet type</dt><dd>{data.customer.outlet_type ?? "—"}</dd>
                <dt>Loyalty</dt><dd>{data.customer.loyalty_program ?? "—"}</dd>
                <dt>Credit term</dt><dd>{data.customer.credit_term} days</dd>
                <dt>First seen</dt><dd>{data.customer.first_seen_on ?? "—"}</dd>
              </dl>
            </div>

            <div className="card" style={{ marginBottom: 14 }}>
              <h3 style={{ marginBottom: 8 }}>Flags on this outlet ({data.violations.length})</h3>
              {data.violations.length === 0 ? (
                <p className="note">No rule flagged this outlet in the latest check.</p>
              ) : (
                <table>
                  <thead><tr><th>Rule</th><th>Salesman</th><th>Reason</th></tr></thead>
                  <tbody>
                    {data.violations.map((v) => (
                      <tr key={v.violation_id}>
                        <td>{v.rule_title}</td>
                        <td>{v.salesman_name ?? "—"}</td>
                        <td>{v.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="card" style={{ marginBottom: 14 }}>
              <h3 style={{ marginBottom: 8 }}>Open documents ({data.documents.length})</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Type</th><th>Bill</th><th>Date</th><th>Salesman</th>
                      <th className="num">Amount</th><th className="num">Balance</th><th className="num">Overdue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.documents.map((d) => (
                      <tr key={`${d.document_type}-${d.document_no}`}>
                        <td>{d.document_type}</td>
                        <td>{d.document_no}</td>
                        <td>{d.document_date ?? "—"}</td>
                        <td>{d.salesman_name ?? "—"}</td>
                        <td className="num">{money(d.amount)}</td>
                        <td className="num">{money(d.balance)}</td>
                        <td className="num">{d.overdue_days ?? d.due_days ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {data.monthly_sales.length > 0 && (
              <div className="card">
                <h3 style={{ marginBottom: 8 }}>Monthly sales</h3>
                <table>
                  <thead><tr><th>Month</th><th className="num">Bills</th><th className="num">Value</th></tr></thead>
                  <tbody>
                    {data.monthly_sales.map((m) => (
                      <tr key={m.month_start}>
                        <td>{m.month_start}</td>
                        <td className="num">{m.invoices}</td>
                        <td className="num">{money(m.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}