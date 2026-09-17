"""Rule engine.

Design
------
* Every rule is one parameterised SQL statement of the shape
  ``INSERT INTO rule_violation (...) SELECT ...`` reading from ``scope``.
* ``scope`` is a temp table materialised once per run from ``sg.f_scope()``,
  which applies the global filters (small-bill ignore, cigarette exclusion,
  PP-beat tolerance) exactly once. A rule can therefore never "forget" a
  global filter.
* Parameters come from ``sg.rule.params`` (jsonb), so thresholds are edited in
  the UI, never in code.
* Adding a rule = adding one entry to RULES + one seed row. Nothing else.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..db import conn


@dataclass(frozen=True)
class Rule:
    code: str
    sql: str
    needs_sales_data: bool = False


# --------------------------------------------------------------------- helpers

SCOPE_SQL = """
CREATE TEMP TABLE scope ON COMMIT DROP AS
SELECT * FROM sg.f_scope(
    %(snapshot_id)s, %(min_balance)s, %(exclude_cigarette)s,
    %(cig_salesman_prefixes)s, %(cig_salesman_names)s, %(cig_locations)s,
    %(use_source_ignore_flag)s, %(pp_tolerance_days)s, %(pp_salesman_types)s)
"""

# psycopg sends one statement per execute(); indexes are built separately
SCOPE_INDEXES = (
    "CREATE INDEX ON scope (customer_code)",
    "CREATE INDEX ON scope (salesman_key)",
    "CREATE INDEX ON scope (document_type)",
    "ANALYZE scope",
)

# every rule INSERT uses this column list
VIOLATION_COLS = (
    "run_id, rule_code, customer_code, salesman_key, doc_key, document_no, "
    "document_date, amount, balance, overdue_days, severity, reason, details"
)


def _exception_filter(alias: str = "s") -> str:
    """SQL fragment suppressing rows covered by an active rule_exception."""
    return f"""
        AND NOT EXISTS (
            SELECT 1 FROM sg.rule_exception x
            WHERE x.is_active
              AND x.rule_code = %(rule_code)s
              AND (x.customer_code IS NULL OR x.customer_code = {alias}.customer_code)
              AND (x.salesman_key  IS NULL OR x.salesman_key  = {alias}.salesman_key)
        )"""


# ----------------------------------------------------------------- the rules

RULES: dict[str, Rule] = {}


# --- R01 DBN overdue -------------------------------------------------------
RULES["R01_DBN_OVERDUE"] = Rule(
    "R01_DBN_OVERDUE",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('%%s %%s pending %%s days (>= %%s), balance Rs.%%s',
              s.document_type, s.document_no, s.eff_overdue, %(days)s, s.balance),
       jsonb_build_object('document_type', s.document_type, 'due_days', s.due_days,
                          'beat', s.raw_beat, 'trigger', 'dbn_age')
FROM scope s
WHERE (s.document_type = ANY (%(document_types)s)
       OR EXISTS (SELECT 1 FROM unnest(%(document_no_prefixes)s::text[]) p
                   WHERE upper(s.document_no) LIKE upper(p) || '%%'))
  AND s.balance > %(min_amount)s
  AND s.eff_overdue >= %(days)s
  {_exception_filter()}
""",
)

# --- R02 same-salesman double bill ----------------------------------------
RULES["R02_SAME_SALESMAN_DOUBLE_BILL"] = Rule(
    "R02_SAME_SALESMAN_DOUBLE_BILL",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH billable AS (
    SELECT * FROM scope s
    WHERE NOT (s.document_type = ANY (%(excluded_document_types)s))
      AND s.balance > %(min_balance)s
), grouped AS (
    SELECT customer_code, salesman_key,
           count(*) AS pending_bills,
           sum(balance) AS pending_balance,
           max(eff_overdue) AS oldest_overdue,
           min(eff_overdue) AS newest_overdue,
           min(document_date) AS earliest_doc
    FROM billable
    GROUP BY 1,2
    HAVING count(*) >= %(min_documents)s
       AND max(eff_overdue) <= %(max_overdue_days)s
)
SELECT %(run_id)s, %(rule_code)s, b.customer_code, b.salesman_key, b.doc_key,
       b.document_no, b.document_date, b.amount, b.balance, b.eff_overdue, %(severity)s,
       format('%%s pending bills from the same salesman (oldest %%s days, total Rs.%%s)',
              g.pending_bills, g.oldest_overdue, round(g.pending_balance)),
       jsonb_build_object('pending_bills', g.pending_bills,
                          'pending_balance', g.pending_balance,
                          'oldest_overdue', g.oldest_overdue)
FROM grouped g
JOIN billable b USING (customer_code, salesman_key)
WHERE true {_exception_filter('b')}
""",
)

# --- R03 cross-salesman credit flag ---------------------------------------
RULES["R03_CROSS_SALESMAN_CREDIT_BLOCK"] = Rule(
    "R03_CROSS_SALESMAN_CREDIT_BLOCK",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH stuck AS (   -- per customer: which salesman is badly overdue, and by how much
    SELECT customer_code, salesman_key, salesman_name,
           max(eff_overdue) AS worst_overdue, sum(balance) AS stuck_balance
    FROM scope
    WHERE balance > %(min_balance)s AND eff_overdue > %(overdue_days)s
    GROUP BY 1,2,3
), fresh AS (     -- per customer: salesmen still billing
    SELECT DISTINCT customer_code, salesman_key, doc_key, document_no, document_date,
           amount, balance, eff_overdue
    FROM scope
    WHERE document_type = 'INV' AND eff_overdue <= %(overdue_days)s
)
SELECT %(run_id)s, %(rule_code)s, f.customer_code, f.salesman_key, f.doc_key,
       f.document_no, f.document_date, f.amount, f.balance, f.eff_overdue, %(severity)s,
       format('Billing continues while %%s is overdue %%s days (Rs.%%s) at the same shop',
              st.salesman_name, st.worst_overdue, round(st.stuck_balance)),
       jsonb_build_object('blocking_salesman', st.salesman_name,
                          'blocking_overdue_days', st.worst_overdue,
                          'blocking_balance', st.stuck_balance)
FROM fresh f
JOIN stuck st ON st.customer_code = f.customer_code AND st.salesman_key <> f.salesman_key
WHERE true {_exception_filter('f')}
""",
)

# --- R04 stock dumping -----------------------------------------------------
RULES["R04_STOCK_DUMPING"] = Rule(
    "R04_STOCK_DUMPING",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH snap AS (SELECT snapshot_date FROM sg.snapshot WHERE snapshot_id = %(snapshot_id)s),
hist AS (
    SELECT m.customer_code,
           sum(m.total_amount)  AS hist_amount,
           sum(m.invoice_count) AS hist_invoices,
           count(DISTINCT m.month_start) AS months
    FROM sg.monthly_sales m, snap
    WHERE m.month_start >= (date_trunc('month', snap.snapshot_date)
                            - (%(months)s || ' months')::interval)::date
      AND m.month_start <  date_trunc('month', snap.snapshot_date)::date
    GROUP BY 1
), base AS (
    SELECT customer_code,
           CASE WHEN %(average_basis)s = 'per_invoice'
                THEN hist_amount / NULLIF(hist_invoices, 0)
                ELSE hist_amount / NULLIF(%(months)s, 0)
           END AS avg_value,
           hist_amount, hist_invoices, months
    FROM hist
)
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('Bill Rs.%%s is %%s%%%% of this outlet''s %%s-month average Rs.%%s',
              round(s.amount), round(100 * s.amount / b.avg_value),
              b.months, round(b.avg_value)),
       jsonb_build_object('avg_value', round(b.avg_value, 2), 'basis', %(average_basis)s,
                          'months', b.months, 'ratio_pct', round(100 * s.amount / b.avg_value))
FROM scope s
JOIN base b ON b.customer_code = s.customer_code
WHERE s.document_type = 'INV'
  AND s.amount >= %(min_invoice_amount)s
  AND s.due_days <= %(recent_days)s
  AND b.avg_value > 0
  AND b.months >= %(months)s          -- need a full window before judging an outlet
  AND s.amount > b.avg_value * (1 + %(threshold_pct)s / 100.0)
  {_exception_filter()}
""",
    needs_sales_data=True,
)

# --- R05 underperforming salesman (collection) ----------------------------
RULES["R05_UNDERPERFORMING_SALESMAN"] = Rule(
    "R05_UNDERPERFORMING_SALESMAN",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH performer AS (   -- salesman A: has a fresh bill on this shop
    SELECT customer_code, salesman_key, salesman_name,
           min(due_days) AS days_since_bill, max(document_date) AS latest_bill
    FROM scope
    WHERE document_type = 'INV' AND due_days <= %(window_days)s
    GROUP BY 1,2,3
), laggard AS (       -- salesman B: old unpaid bills, not yet severely overdue
    SELECT customer_code, salesman_key, doc_key, document_no, document_date,
           amount, balance, eff_overdue
    FROM scope
    WHERE document_type = 'INV'
      AND balance > %(min_balance)s
      AND eff_overdue < %(max_overdue_days)s
      AND due_days > %(window_days)s
)
SELECT %(run_id)s, %(rule_code)s, l.customer_code, l.salesman_key, l.doc_key,
       l.document_no, l.document_date, l.amount, l.balance, l.eff_overdue, %(severity)s,
       format('%%s billed this shop %%s day(s) ago; this bill is still uncollected after %%s days',
              p.salesman_name, p.days_since_bill, l.eff_overdue),
       jsonb_build_object('peer_salesman', p.salesman_name,
                          'peer_latest_bill', p.latest_bill,
                          'peer_days_since_bill', p.days_since_bill)
FROM laggard l
JOIN performer p ON p.customer_code = l.customer_code AND p.salesman_key <> l.salesman_key
WHERE true {_exception_filter('l')}
""",
)

# --- R06 lost potential sale (category coverage) --------------------------
RULES["R06_LOST_POTENTIAL_SALE"] = Rule(
    "R06_LOST_POTENTIAL_SALE",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH billed AS (
    SELECT s.customer_code, sc.category, min(s.due_days) AS days_ago,
           max(s.doc_key) AS doc_key, max(s.document_no) AS document_no,
           max(s.document_date) AS document_date, max(s.salesman_key) AS salesman_key
    FROM scope s
    JOIN sg.salesman_category sc ON sc.salesman_key = s.salesman_key
    WHERE s.document_type = 'INV'
    GROUP BY 1,2
), recent AS (SELECT * FROM billed WHERE days_ago <= %(window_days)s)
SELECT %(run_id)s, %(rule_code)s, r.customer_code, r.salesman_key, r.doc_key,
       r.document_no, r.document_date, NULL, NULL, NULL, %(severity)s,
       format('%%s billed %%s day(s) ago but %%s was not billed at this outlet',
              r.category, r.days_ago, a.expects_category),
       jsonb_build_object('billed_category', r.category,
                          'missing_category', a.expects_category,
                          'days_ago', r.days_ago)
FROM recent r
JOIN sg.category_affinity a ON a.category = r.category
LEFT JOIN billed b ON b.customer_code = r.customer_code AND b.category = a.expects_category
WHERE b.customer_code IS NULL
  {_exception_filter('r')}
""",
)

# --- R07 low outstanding ratio --------------------------------------------
RULES["R07_LOW_OUTSTANDING_RATIO"] = Rule(
    "R07_LOW_OUTSTANDING_RATIO",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('Only Rs.%%s of Rs.%%s left open (%%s%%%%) - confirm no short/return pending',
              round(s.balance), round(s.amount), round(100 * s.balance / s.amount)),
       jsonb_build_object('ratio_pct', round(100 * s.balance / s.amount, 2))
FROM scope s
WHERE s.document_type = 'INV'
  AND s.amount >= %(min_invoice_amount)s
  AND s.balance > 0
  AND s.balance <= s.amount * %(ratio_pct)s / 100.0
  {_exception_filter()}
""",
)

# --- R08 chronic default customer -----------------------------------------
RULES["R08_CHRONIC_DEFAULT_CUSTOMER"] = Rule(
    "R08_CHRONIC_DEFAULT_CUSTOMER",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH per_sman AS (
    SELECT customer_code, salesman_key, count(*) AS bills,
           min(eff_overdue) AS min_overdue, sum(balance) AS balance
    FROM scope
    WHERE document_type IN ('INV','OPN','DBN') AND balance > %(min_balance)s
    GROUP BY 1,2
), chronic AS (
    SELECT customer_code, count(*) AS salesmen, sum(balance) AS total_balance,
           min(min_overdue) AS min_overdue
    FROM per_sman
    GROUP BY 1
    HAVING count(*) >= %(min_salesmen)s
       AND max(bills) = 1
       AND min(min_overdue) > %(overdue_days)s
)
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('All %%s salesmen have exactly one stuck bill here; total Rs.%%s, all over %%s days',
              c.salesmen, round(c.total_balance), %(overdue_days)s),
       jsonb_build_object('salesmen', c.salesmen, 'total_balance', c.total_balance,
                          'min_overdue', c.min_overdue)
FROM chronic c
JOIN scope s ON s.customer_code = c.customer_code AND s.balance > %(min_balance)s
WHERE true {_exception_filter()}
""",
)

# --- R09 over-30-day invoice list ------------------------------------------
RULES["R09_OVER_30_DAY_INVOICE"] = Rule(
    "R09_OVER_30_DAY_INVOICE",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('%%s overdue %%s days, Rs.%%s outstanding', s.document_type, s.eff_overdue, round(s.balance)),
       jsonb_build_object('tolerance_days', s.tolerance_days, 'beat', s.raw_beat,
                          'due_days', s.due_days)
FROM scope s
WHERE s.document_type = ANY (%(document_types)s)
  AND s.balance > %(min_balance)s
  AND s.eff_overdue > %(overdue_days)s
  {_exception_filter()}
""",
)

# --- R14 high uncollected ratio (client rule 6: 80-100% not collected) -----
RULES["R14_HIGH_UNCOLLECTED_RATIO"] = Rule(
    "R14_HIGH_UNCOLLECTED_RATIO",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH pair AS (
    SELECT customer_code, salesman_key,
           sum(amount) AS amount, sum(balance) AS balance, max(eff_overdue) AS worst_overdue,
           max(doc_key) AS doc_key
    FROM scope
    WHERE document_type = 'INV' AND eff_overdue > %(overdue_days)s
    GROUP BY 1,2
    HAVING sum(amount) >= %(min_invoice_amount)s AND sum(amount) > 0
)
SELECT %(run_id)s, %(rule_code)s, p.customer_code, p.salesman_key, p.doc_key,
       NULL, NULL, p.amount, p.balance, p.worst_overdue, %(severity)s,
       format('%%s%%%% of Rs.%%s still uncollected after %%s days',
              round(100 * p.balance / p.amount), round(p.amount), p.worst_overdue),
       jsonb_build_object('uncollected_pct', round(100 * p.balance / p.amount, 2))
FROM pair p
WHERE 100 * p.balance / p.amount BETWEEN %(min_ratio_pct)s AND %(max_ratio_pct)s
  {_exception_filter('p')}
""",
)

# --- R15 dormant customer --------------------------------------------------
RULES["R15_DORMANT_CUSTOMER"] = Rule(
    "R15_DORMANT_CUSTOMER",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH activity AS (
    SELECT customer_code, min(due_days) AS days_since_last_bill,
           max(due_days) AS days_since_first_bill,
           max(doc_key) AS doc_key, max(salesman_key) AS salesman_key
    FROM scope
    WHERE document_type = 'INV'
    GROUP BY 1
)
SELECT %(run_id)s, %(rule_code)s, a.customer_code, a.salesman_key, a.doc_key,
       NULL, NULL, NULL, NULL, NULL, %(severity)s,
       format('No billing for %%s days at this outlet', a.days_since_last_bill),
       jsonb_build_object('days_since_last_bill', a.days_since_last_bill)
FROM activity a
WHERE a.days_since_last_bill >= %(dormant_days)s
  AND a.days_since_first_bill <= %(was_active_within_days)s
  {_exception_filter('a')}
""",
)

# --- R16 sudden drop in sales ---------------------------------------------
RULES["R16_SUDDEN_DROP"] = Rule(
    "R16_SUDDEN_DROP",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH snap AS (SELECT snapshot_date FROM sg.snapshot WHERE snapshot_id = %(snapshot_id)s),
cur AS (
    SELECT m.customer_code, sum(m.total_amount) AS cur_amount
    FROM sg.monthly_sales m, snap
    WHERE m.month_start = date_trunc('month', snap.snapshot_date)::date
    GROUP BY 1
), hist AS (
    SELECT m.customer_code, sum(m.total_amount) / %(months)s AS avg_amount
    FROM sg.monthly_sales m, snap
    WHERE m.month_start >= (date_trunc('month', snap.snapshot_date)
                            - (%(months)s || ' months')::interval)::date
      AND m.month_start < date_trunc('month', snap.snapshot_date)::date
    GROUP BY 1
)
SELECT %(run_id)s, %(rule_code)s, h.customer_code,
       (SELECT max(salesman_key) FROM scope s WHERE s.customer_code = h.customer_code),
       NULL, NULL, NULL, NULL, NULL, NULL, %(severity)s,
       format('This month Rs.%%s vs %%s-month average Rs.%%s',
              round(coalesce(c.cur_amount,0)), %(months)s, round(h.avg_amount)),
       jsonb_build_object('current_amount', coalesce(c.cur_amount,0),
                          'avg_amount', round(h.avg_amount,2))
FROM hist h
LEFT JOIN cur c ON c.customer_code = h.customer_code
WHERE h.avg_amount > 0
  AND coalesce(c.cur_amount,0) < h.avg_amount * %(drop_below_pct)s / 100.0
  AND EXISTS (SELECT 1 FROM scope s WHERE s.customer_code = h.customer_code)
""",
    needs_sales_data=True,
)

# --- R17 beat coverage gap -------------------------------------------------
RULES["R17_BEAT_COVERAGE_GAP"] = Rule(
    "R17_BEAT_COVERAGE_GAP",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH per_cust AS (
    SELECT beat_key, raw_beat, customer_code, min(due_days) AS days_since_bill,
           max(doc_key) AS doc_key, max(salesman_key) AS salesman_key
    FROM scope
    WHERE document_type = 'INV' AND beat_key IS NOT NULL
    GROUP BY 1,2,3
), per_beat AS (
    SELECT beat_key, min(days_since_bill) AS beat_freshest
    FROM per_cust GROUP BY 1
)
SELECT %(run_id)s, %(rule_code)s, pc.customer_code, pc.salesman_key, pc.doc_key,
       NULL, NULL, NULL, NULL, NULL, %(severity)s,
       format('Beat %%s was served %%s day(s) ago but this outlet was skipped for %%s days',
              pc.raw_beat, pb.beat_freshest, pc.days_since_bill),
       jsonb_build_object('beat', pc.raw_beat, 'beat_freshest_days', pb.beat_freshest,
                          'days_since_bill', pc.days_since_bill)
FROM per_cust pc
JOIN per_beat pb USING (beat_key)
WHERE pc.days_since_bill >= %(gap_days)s
  AND pb.beat_freshest < %(gap_days)s
  {_exception_filter('pc')}
""",
)

# --- R18 new customer risk -------------------------------------------------
RULES["R18_NEW_CUSTOMER_RISK"] = Rule(
    "R18_NEW_CUSTOMER_RISK",
    f"""
INSERT INTO sg.rule_violation ({VIOLATION_COLS})
WITH first_bill AS (
    -- first_seen_on accumulates across every snapshot ever loaded, so this
    -- gets sharper the longer SalesGuard runs. On day 1 it can only see the
    -- oldest unpaid bill in the file.
    SELECT c.customer_code,
           (SELECT snapshot_date FROM sg.snapshot WHERE snapshot_id = %(snapshot_id)s)
             - c.first_seen_on AS oldest_bill_age
    FROM sg.dim_customer c
    WHERE c.first_seen_on IS NOT NULL
)
SELECT %(run_id)s, %(rule_code)s, s.customer_code, s.salesman_key, s.doc_key,
       s.document_no, s.document_date, s.amount, s.balance, s.eff_overdue, %(severity)s,
       format('New outlet (first bill %%s days ago) already overdue %%s days, Rs.%%s',
              f.oldest_bill_age, s.eff_overdue, round(s.balance)),
       jsonb_build_object('oldest_bill_age', f.oldest_bill_age)
FROM scope s
JOIN first_bill f ON f.customer_code = s.customer_code
WHERE f.oldest_bill_age <= %(new_within_days)s
  AND s.document_type = 'INV'
  AND s.balance > %(min_balance)s
  AND s.eff_overdue > %(overdue_days)s
  {_exception_filter()}
""",
)


# ----------------------------------------------------------------- the runner

DEFAULT_GLOBALS = {
    "min_balance": 10,
    "exclude_cigarette": True,
    "cig_salesman_prefixes": ["TH"],
    "cig_salesman_names": ["MANIKANDAN VAN", "NANDAKUMAR VAN"],
    "cig_locations": ["CBE"],
    "use_source_ignore_flag": False,
    "pp_tolerance_days": 0,
    "pp_salesman_types": ["Grocery 2 Ds"],
}


def _globals(cur) -> dict:
    cur.execute("SELECT params FROM sg.global_config WHERE id")
    row = cur.fetchone()
    merged = dict(DEFAULT_GLOBALS)
    if row:
        merged.update(row["params"])
    return merged


def has_sales_data(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM sg.monthly_sales) AS ok")
    return bool(cur.fetchone()["ok"])


def run_rules(snapshot_id: int, only: list[str] | None = None) -> dict:
    """Evaluate every enabled rule against one snapshot. Returns a run summary."""
    with conn() as c:
        with c.cursor() as cur:
            g = _globals(cur)
            cur.execute(
                """INSERT INTO sg.rule_run (snapshot_id, global_params, status)
                   VALUES (%s, %s, 'running') RETURNING run_id""",
                (snapshot_id, json.dumps(g)),
            )
            run_id = cur.fetchone()["run_id"]

            # one scope for the whole run
            cur.execute(SCOPE_SQL, {"snapshot_id": snapshot_id, **{
                k: g[k] for k in (
                    "min_balance", "exclude_cigarette", "cig_salesman_prefixes",
                    "cig_salesman_names", "cig_locations", "use_source_ignore_flag",
                    "pp_tolerance_days", "pp_salesman_types")
            }})
            for stmt in SCOPE_INDEXES:
                cur.execute(stmt)
            cur.execute("SELECT count(*) AS n FROM scope")
            scope_rows = cur.fetchone()["n"]
            sales_ready = has_sales_data(cur)

            cur.execute("SELECT count(*) AS n FROM sg.snapshot")
            snapshots_loaded = cur.fetchone()["n"]

            cur.execute(
                """SELECT rule_code, params, severity, needs_sales_data,
                          coalesce(needs_history, false) AS needs_history
                   FROM sg.rule WHERE is_enabled ORDER BY sort_order"""
            )
            wanted = cur.fetchall()

            summary = []
            for r in wanted:
                code = r["rule_code"]
                if only and code not in only:
                    continue
                spec = RULES.get(code)
                if spec is None:
                    _detail(cur, run_id, code, "skipped", 0, None, r["params"],
                            "No implementation registered")
                    continue
                if r["needs_history"] and snapshots_loaded < 2:
                    _detail(cur, run_id, code, "skipped", 0, None, r["params"],
                            "Needs at least two loaded snapshots to tell a new outlet "
                            "from an old one")
                    summary.append({"rule_code": code, "status": "skipped", "hits": 0})
                    continue
                if spec.needs_sales_data and not sales_ready:
                    _detail(cur, run_id, code, "skipped", 0, None, r["params"],
                            "Waiting for monthly sales data")
                    summary.append({"rule_code": code, "status": "skipped", "hits": 0})
                    continue

                binds = {
                    "run_id": run_id,
                    "rule_code": code,
                    "snapshot_id": snapshot_id,
                    "severity": r["severity"],
                    **g,
                    **(r["params"] or {}),
                }
                t0 = time.perf_counter()
                try:
                    cur.execute(spec.sql, binds)
                    hits = cur.rowcount
                    ms = int((time.perf_counter() - t0) * 1000)
                    _detail(cur, run_id, code, "success", hits, ms, r["params"], None)
                    summary.append({"rule_code": code, "status": "success",
                                    "hits": hits, "ms": ms})
                except Exception as exc:
                    # fail the whole run: half-applied rule output is worse than none
                    raise RuleFailure(code, str(exc)) from exc

            cur.execute(
                """UPDATE sg.rule_run
                      SET status='success', finished_at=now(), scope_rows=%s
                    WHERE run_id=%s""",
                (scope_rows, run_id),
            )
        c.commit()

    return {"run_id": run_id, "snapshot_id": snapshot_id, "scope_rows": scope_rows,
            "sales_data_present": sales_ready, "snapshots_loaded": snapshots_loaded,
            "rules": summary}


class RuleFailure(RuntimeError):
    def __init__(self, rule_code: str, message: str):
        super().__init__(f"{rule_code}: {message}")
        self.rule_code = rule_code


def _detail(cur, run_id, code, status, hits, ms, params, message):
    cur.execute(
        """INSERT INTO sg.rule_run_detail (run_id, rule_code, status, hits, ms, params, message)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (run_id, rule_code) DO UPDATE
             SET status=EXCLUDED.status, hits=EXCLUDED.hits, ms=EXCLUDED.ms,
                 message=EXCLUDED.message""",
        (run_id, code, status, hits, ms, json.dumps(params or {}), message),
    )
