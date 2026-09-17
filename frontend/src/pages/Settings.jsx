import React, { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Settings({ onSaved, onBack }) {
  const [rules, setRules] = useState([]);
  const [config, setConfig] = useState(null);
  const [exceptions, setExceptions] = useState([]);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(null);

  const reload = () =>
    Promise.all([api.rules(), api.config(), api.exceptions()])
      .then(([r, c, e]) => {
        setRules(r);
        setConfig(c.params);
        setExceptions(e);
        setError(null);
      })
      .catch((err) => setError(err.message));

  useEffect(() => {
    reload();
  }, []);

  const saveRule = async (code, body) => {
    try {
      await api.patchRule(code, body);
      setSaved(`${code} saved. Re-check to apply it to today's lists.`);
      await reload();
      onSaved?.();
    } catch (err) {
      setError(err.message);
    }
  };

  const saveConfig = async (params) => {
    try {
      await api.putConfig(params);
      setSaved("Global filters saved. Re-check to apply them.");
      await reload();
      onSaved?.();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <>
      {onBack && (
        <button className="back-link" onClick={onBack}>
          <span className="back-arrow">&#8592;</span> Back to Dashboard
        </button>
      )}
      <div className="topbar">
        <h1>Rule settings</h1>
        <span className="meta">Every threshold here is a setting, not code. Re-check after saving.</span>
      </div>

      {error && <div className="error">{error}</div>}
      {saved && <p className="note">{saved}</p>}

      {config && (
        <div className="card" style={{ marginBottom: 18 }}>
          <h2 style={{ marginBottom: 6 }}>Filters that apply to every rule</h2>
          <p className="note" style={{ marginTop: 0 }}>
            Small bills and cigarette routes are dropped once, before any rule runs.
          </p>
          <GlobalForm config={config} onSave={saveConfig} />
        </div>
      )}

      <div className="card" style={{ marginBottom: 18 }}>
        <h2 style={{ marginBottom: 10 }}>Rules</h2>
        <div className="table-scroll">
          {rules.map((r) => (
            <RuleRow key={r.rule_code} rule={r} onSave={saveRule} />
          ))}
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginBottom: 6 }}>Exceptions</h2>
        <p className="note" style={{ marginTop: 0 }}>
          An outlet or salesman listed here is skipped by that one rule — this is how
          approved multiple billing is handled.
        </p>
        {exceptions.length === 0 ? (
          <p className="note">No exceptions yet.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Rule</th><th>Outlet</th><th>Salesman</th><th>Note</th><th>Active</th><th /></tr>
            </thead>
            <tbody>
              {exceptions.map((e) => (
                <tr key={e.exception_id}>
                  <td>{e.rule_code}</td>
                  <td>{e.customer_name ?? e.customer_code ?? "any"}</td>
                  <td>{e.salesman_name ?? "any"}</td>
                  <td>{e.note ?? "—"}</td>
                  <td>{e.is_active ? "Yes" : "No"}</td>
                  <td>
                    {e.is_active && (
                      <button className="linkbtn" onClick={() => api.dropException(e.exception_id).then(reload)}>
                        Turn off
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <AddException rules={rules} onAdded={reload} onError={setError} />
      </div>
    </>
  );
}

function GlobalForm({ config, onSave }) {
  const [draft, setDraft] = useState(config);

  const set = (key, value) => setDraft({ ...draft, [key]: value });

  return (
    <div>
      <div className="toolbar">
        <label>
          Ignore bills at or below{" "}
          <input
            type="number"
            value={draft.min_balance}
            onChange={(e) => set("min_balance", Number(e.target.value))}
            style={{ width: 90 }}
          />{" "}
          rupees
        </label>
        <label>
          <input
            type="checkbox"
            checked={!!draft.exclude_cigarette}
            onChange={(e) => set("exclude_cigarette", e.target.checked)}
          />{" "}
          Exclude cigarette routes
        </label>
        <label>
          Extra days allowed on PP beats{" "}
          <input
            type="number"
            value={draft.pp_tolerance_days}
            onChange={(e) => set("pp_tolerance_days", Number(e.target.value))}
            style={{ width: 70 }}
          />
        </label>
      </div>
      <div className="toolbar">
        <label>
          Cigarette salesman name prefixes{" "}
          <input
            value={(draft.cig_salesman_prefixes ?? []).join(", ")}
            onChange={(e) =>
              set("cig_salesman_prefixes", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
            }
            style={{ width: 160 }}
          />
        </label>
        <label>
          Cigarette salesmen{" "}
          <input
            value={(draft.cig_salesman_names ?? []).join(", ")}
            onChange={(e) =>
              set("cig_salesman_names", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
            }
            style={{ width: 300 }}
          />
        </label>
        <button className="primary" onClick={() => onSave(draft)}>Save filters</button>
      </div>
    </div>
  );
}

function RuleRow({ rule, onSave }) {
  const [draft, setDraft] = useState(rule.params ?? {});
  const dirty = JSON.stringify(draft) !== JSON.stringify(rule.params ?? {});

  return (
    <div style={{ borderBottom: "1px solid #edf0f3", padding: "12px 0" }}>
      <div className="toolbar" style={{ marginBottom: 6 }}>
        <strong>{rule.title}</strong>
        <span className={`pill ${rule.severity}`}>{rule.severity}</span>
        <span className="note">{rule.rule_code}</span>
        {!rule.implemented && <span className="pill skipped">not implemented</span>}
        <span className="spacer" />
        <label className="toggle-switch" title={rule.is_enabled ? "On" : "Off"}>
          <input
            type="checkbox"
            checked={rule.is_enabled}
            onChange={(e) => onSave(rule.rule_code, { is_enabled: e.target.checked })}
          />
          <span className="toggle-track"><span className="toggle-thumb" /></span>
        </label>
      </div>
      <p className="note" style={{ margin: "0 0 8px" }}>{rule.purpose}</p>
      <div className="toolbar">
        {Object.entries(draft).map(([key, value]) => (
          <label key={key} className="note">
            {key.replace(/_/g, " ")}{" "}
            {typeof value === "boolean" ? (
              <input
                type="checkbox"
                checked={value}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })}
              />
            ) : Array.isArray(value) ? (
              <input
                value={value.join(", ")}
                onChange={(e) =>
                  setDraft({ ...draft, [key]: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })
                }
                style={{ width: 170 }}
              />
            ) : typeof value === "number" ? (
              <input
                type="number"
                value={value}
                onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
                style={{ width: 80 }}
              />
            ) : (
              <input
                value={value ?? ""}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                style={{ width: 120 }}
              />
            )}
          </label>
        ))}
        <button className="ghost" disabled={!dirty} onClick={() => onSave(rule.rule_code, { params: draft })}>
          Save thresholds
        </button>
      </div>
    </div>
  );
}

function AddException({ rules, onAdded, onError }) {
  const [form, setForm] = useState({ rule_code: "R02_SAME_SALESMAN_DOUBLE_BILL", customer_code: "", note: "" });

  const add = async () => {
    try {
      await api.addException({
        rule_code: form.rule_code,
        customer_code: form.customer_code.trim() || null,
        note: form.note.trim() || null,
      });
      setForm({ ...form, customer_code: "", note: "" });
      onAdded();
    } catch (err) {
      onError(err.message);
    }
  };

  return (
    <div className="toolbar" style={{ marginTop: 12 }}>
      <select
        value={form.rule_code}
        onChange={(e) => setForm({ ...form, rule_code: e.target.value })}
        aria-label="Rule to skip"
      >
        {rules.map((r) => (
          <option key={r.rule_code} value={r.rule_code}>{r.title}</option>
        ))}
      </select>
      <input
        placeholder="Customer code"
        value={form.customer_code}
        onChange={(e) => setForm({ ...form, customer_code: e.target.value })}
      />
      <input
        placeholder="Why (optional)"
        value={form.note}
        onChange={(e) => setForm({ ...form, note: e.target.value })}
        style={{ width: 240 }}
      />
      <button className="primary" onClick={add} disabled={!form.customer_code.trim()}>
        Add exception
      </button>
    </div>
  );
}