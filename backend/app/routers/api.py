"""HTTP surface. Thin: validate, delegate, return rows."""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from ..db import fetch_all, fetch_one, execute, conn
from ..ingest import load_snapshot
from ..rules import RULES, RuleFailure, run_rules
from ..schemas import (ExceptionIn, GlobalConfigIn, NoteIn, RuleParamsIn, RunRequest)

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ snapshots

@router.get("/snapshots")
def list_snapshots():
    return fetch_all(
        """SELECT s.*, (SELECT count(*) FROM outstanding_document d
                         WHERE d.snapshot_id = s.snapshot_id) AS documents
           FROM snapshot s ORDER BY snapshot_date DESC, snapshot_id DESC"""
    )


@router.post("/snapshots/upload")
async def upload_snapshot(
    file: UploadFile = File(...),
    snapshot_type: str = Form("daily"),
    snapshot_date: str | None = Form(None),
    run_rules_after: bool = Form(True),
):
    if not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(400, "Upload the Excel outstanding report (.xlsx)")
    tmp = Path(tempfile.mkdtemp()) / file.filename
    with tmp.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        loaded = load_snapshot(
            tmp,
            snapshot_date=date.fromisoformat(snapshot_date) if snapshot_date else None,
            snapshot_type=snapshot_type,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    if run_rules_after:
        loaded["run"] = _run(loaded["snapshot_id"], None)
    return loaded


# ---------------------------------------------------------------------- runs

def _run(snapshot_id: int | None, only: list[str] | None):
    if snapshot_id is None:
        row = fetch_one("SELECT snapshot_id FROM snapshot ORDER BY snapshot_date DESC, snapshot_id DESC LIMIT 1")
        if not row:
            raise HTTPException(409, "No snapshot loaded yet - upload an outstanding file first")
        snapshot_id = row["snapshot_id"]
    try:
        return run_rules(snapshot_id, only)
    except RuleFailure as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/runs")
def create_run(req: RunRequest):
    return _run(req.snapshot_id, req.only)


@router.get("/runs")
def list_runs(limit: int = 20):
    return fetch_all(
        """SELECT r.*, s.snapshot_date, s.snapshot_type,
                  (SELECT count(*) FROM rule_violation v WHERE v.run_id = r.run_id) AS violations
           FROM rule_run r JOIN snapshot s USING (snapshot_id)
           ORDER BY r.run_id DESC LIMIT %s""",
        (limit,),
    )


def _latest_run_id() -> int:
    row = fetch_one("SELECT run_id FROM rule_run WHERE status='success' ORDER BY run_id DESC LIMIT 1")
    if not row:
        raise HTTPException(409, "No successful rule run yet")
    return row["run_id"]


# ------------------------------------------------------------------ dashboard

@router.get("/dashboard")
def dashboard(run_id: int | None = None):
    run_id = run_id or _latest_run_id()
    run = fetch_one(
        """SELECT r.*, s.snapshot_date, s.snapshot_type, s.source_file
           FROM rule_run r JOIN snapshot s USING (snapshot_id) WHERE run_id=%s""",
        (run_id,),
    )
    by_rule = fetch_all(
        """SELECT ru.rule_code, ru.title, ru.purpose, ru.data_note, ru.audience,
                  ru.rule_group, ru.severity, ru.is_enabled,
                  d.status, coalesce(d.hits,0) AS hits, d.ms, d.message,
                  (SELECT count(DISTINCT v.customer_code) FROM rule_violation v
                    WHERE v.run_id=%s AND v.rule_code=ru.rule_code) AS customers,
                  (SELECT coalesce(sum(v.balance),0) FROM rule_violation v
                    WHERE v.run_id=%s AND v.rule_code=ru.rule_code) AS exposure
           FROM rule ru
           LEFT JOIN rule_run_detail d ON d.run_id=%s AND d.rule_code=ru.rule_code
           ORDER BY ru.sort_order""",
        (run_id, run_id, run_id),
    )
    totals = fetch_one(
        """SELECT count(*) AS violations,
                  count(DISTINCT customer_code) AS customers,
                  count(DISTINCT salesman_key) AS salesmen,
                  coalesce(sum(balance),0) AS exposure
           FROM rule_violation WHERE run_id=%s""",
        (run_id,),
    )
    worst_salesmen = fetch_all(
        """SELECT s.salesman_name, s.salesman_type, count(*) AS flags,
                  coalesce(sum(v.balance),0) AS exposure
           FROM rule_violation v JOIN dim_salesman s USING (salesman_key)
           WHERE v.run_id=%s GROUP BY 1,2 ORDER BY exposure DESC LIMIT 10""",
        (run_id,),
    )
    worst_customers = fetch_all(
        """SELECT v.customer_code, c.customer_name, c.channel_type, count(*) AS flags,
                  coalesce(sum(v.balance),0) AS exposure
           FROM rule_violation v JOIN dim_customer c USING (customer_code)
           WHERE v.run_id=%s GROUP BY 1,2,3 ORDER BY exposure DESC LIMIT 10""",
        (run_id,),
    )
    return {"run": run, "totals": totals, "by_rule": by_rule,
            "top_salesmen": worst_salesmen, "top_customers": worst_customers}


# ----------------------------------------------------------------- violations

def _violation_query(run_id: int, rule_code: str | None, search: str | None,
                     salesman_key: int | None, severity: str | None,
                     limit: int, offset: int):
    where = ["run_id = %(run_id)s"]
    args: dict = {"run_id": run_id, "limit": limit, "offset": offset}
    if rule_code:
        where.append("rule_code = %(rule_code)s")
        args["rule_code"] = rule_code
    if salesman_key:
        where.append("salesman_key = %(salesman_key)s")
        args["salesman_key"] = salesman_key
    if severity:
        where.append("severity = %(severity)s")
        args["severity"] = severity
    if search:
        where.append("(customer_name ILIKE %(q)s OR customer_code ILIKE %(q)s "
                     "OR salesman_name ILIKE %(q)s OR document_no ILIKE %(q)s)")
        args["q"] = f"%{search}%"
    clause = " AND ".join(where)
    return clause, args


@router.get("/violations")
def violations(
    run_id: int | None = None,
    rule_code: str | None = None,
    search: str | None = None,
    salesman_key: int | None = None,
    severity: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
):
    run_id = run_id or _latest_run_id()
    clause, args = _violation_query(run_id, rule_code, search, salesman_key, severity, limit, offset)
    rows = fetch_all(
        f"""SELECT * FROM v_violation WHERE {clause}
            ORDER BY balance DESC NULLS LAST, overdue_days DESC NULLS LAST
            LIMIT %(limit)s OFFSET %(offset)s""",
        args,
    )
    total = fetch_one(f"SELECT count(*) AS n, coalesce(sum(balance),0) AS exposure "
                      f"FROM v_violation WHERE {clause}", args)
    return {"run_id": run_id, "total": total["n"], "exposure": total["exposure"], "rows": rows}


@router.get("/violations.csv")
def violations_csv(
    run_id: int | None = None,
    rule_code: str | None = None,
    search: str | None = None,
    salesman_key: int | None = None,
    severity: str | None = None,
):
    run_id = run_id or _latest_run_id()
    clause, args = _violation_query(run_id, rule_code, search, salesman_key, severity, 100000, 0)
    rows = fetch_all(
        f"""SELECT rule_code, rule_title, severity, customer_code, customer_name, channel_type,
                   salesman_name, salesman_type, raw_beat AS beat, document_no, document_date,
                   amount, balance, overdue_days, reason
            FROM v_violation WHERE {clause}
            ORDER BY rule_code, balance DESC NULLS LAST""",
        args,
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["rule_code"])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    name = f"salesguard_{rule_code or 'all'}_run{run_id}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/customers/{customer_code}")
def customer_detail(customer_code: str, run_id: int | None = None):
    run_id = run_id or _latest_run_id()
    customer = fetch_one("SELECT * FROM dim_customer WHERE customer_code=%s", (customer_code,))
    if not customer:
        raise HTTPException(404, "Customer not found")
    docs = fetch_all(
        """SELECT d.document_type, d.document_no, d.document_date, d.amount, d.balance,
                  d.due_days, d.overdue_days, d.invoice_status, s.salesman_name, b.raw_beat
           FROM outstanding_document d
           LEFT JOIN dim_salesman s USING (salesman_key)
           LEFT JOIN dim_beat b USING (beat_key)
           WHERE d.snapshot_id = (SELECT snapshot_id FROM rule_run WHERE run_id=%s)
             AND d.customer_code=%s
           ORDER BY d.document_date DESC""",
        (run_id, customer_code),
    )
    flags = fetch_all(
        "SELECT * FROM v_violation WHERE run_id=%s AND customer_code=%s ORDER BY rule_code",
        (run_id, customer_code),
    )
    sales = fetch_all(
        """SELECT month_start, sum(total_amount) AS amount, sum(invoice_count) AS invoices
           FROM monthly_sales WHERE customer_code=%s GROUP BY 1 ORDER BY 1 DESC LIMIT 6""",
        (customer_code,),
    )
    return {"customer": customer, "documents": docs, "violations": flags, "monthly_sales": sales}


@router.get("/salesmen")
def salesmen(run_id: int | None = None):
    run_id = run_id or _latest_run_id()
    return fetch_all(
        """SELECT s.salesman_key, s.salesman_name, s.salesman_type,
                  count(v.violation_id) AS flags,
                  coalesce(sum(v.balance),0) AS exposure,
                  count(DISTINCT v.customer_code) AS customers
           FROM dim_salesman s
           LEFT JOIN rule_violation v ON v.salesman_key=s.salesman_key AND v.run_id=%s
           GROUP BY 1,2,3 ORDER BY exposure DESC""",
        (run_id,),
    )


# --------------------------------------------------------------------- config

@router.get("/rules")
def list_rules():
    return fetch_all(
        """SELECT r.*, (r.rule_code = ANY (%s)) AS implemented
           FROM rule r ORDER BY sort_order""",
        (list(RULES.keys()),),
    )


@router.patch("/rules/{rule_code}")
def update_rule(rule_code: str, body: RuleParamsIn):
    current = fetch_one("SELECT * FROM rule WHERE rule_code=%s", (rule_code,))
    if not current:
        raise HTTPException(404, "Unknown rule")
    params = {**(current["params"] or {}), **(body.params or {})}
    execute(
        """UPDATE rule SET params=%s,
                  is_enabled=coalesce(%s, is_enabled),
                  severity=coalesce(%s, severity),
                  updated_at=now()
            WHERE rule_code=%s""",
        (json.dumps(params), body.is_enabled, body.severity, rule_code),
    )
    return fetch_one("SELECT * FROM rule WHERE rule_code=%s", (rule_code,))


@router.get("/config")
def get_config():
    return fetch_one("SELECT params, updated_at FROM global_config WHERE id")


@router.put("/config")
def put_config(body: GlobalConfigIn):
    current = fetch_one("SELECT params FROM global_config WHERE id")
    merged = {**(current["params"] if current else {}), **body.params}
    execute(
        """INSERT INTO global_config (id, params) VALUES (true, %s)
           ON CONFLICT (id) DO UPDATE SET params=EXCLUDED.params, updated_at=now()""",
        (json.dumps(merged),),
    )
    return {"params": merged}


@router.get("/exceptions")
def list_exceptions():
    return fetch_all(
        """SELECT e.*, c.customer_name, s.salesman_name
           FROM rule_exception e
           LEFT JOIN dim_customer c USING (customer_code)
           LEFT JOIN dim_salesman s USING (salesman_key)
           ORDER BY e.exception_id DESC"""
    )


@router.post("/exceptions")
def add_exception(body: ExceptionIn):
    if not body.customer_code and not body.salesman_key:
        raise HTTPException(400, "Give a customer, a salesman, or both")
    row = fetch_one(
        """INSERT INTO rule_exception (rule_code, customer_code, salesman_key, note)
           VALUES (%s,%s,%s,%s) RETURNING *""",
        (body.rule_code, body.customer_code, body.salesman_key, body.note),
    )
    return row


@router.delete("/exceptions/{exception_id}")
def drop_exception(exception_id: int):
    execute("UPDATE rule_exception SET is_active=false WHERE exception_id=%s", (exception_id,))
    return {"exception_id": exception_id, "is_active": False}


@router.post("/violations/{violation_id}/notes")
def add_note(violation_id: int, body: NoteIn):
    return fetch_one(
        """INSERT INTO violation_note (violation_id, author, note)
           VALUES (%s,%s,%s) RETURNING *""",
        (violation_id, body.author, body.note),
    )


@router.get("/violations/{violation_id}/notes")
def get_notes(violation_id: int):
    return fetch_all(
        "SELECT * FROM violation_note WHERE violation_id=%s ORDER BY created_at",
        (violation_id,),
    )


# ------------------------------------------------------- category / salesman map

@router.get("/masters/categories")
def categories():
    return {
        "categories": fetch_all("SELECT * FROM dim_category ORDER BY category"),
        "affinity": fetch_all("SELECT * FROM category_affinity ORDER BY category"),
        "mapped_salesmen": fetch_all(
            """SELECT sc.salesman_key, s.salesman_name, sc.category
               FROM salesman_category sc JOIN dim_salesman s USING (salesman_key)
               ORDER BY s.salesman_name"""
        ),
    }


@router.put("/masters/salesman-category")
def map_salesman(salesman_key: int, category: str):
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO salesman_category (salesman_key, category) VALUES (%s,%s)
               ON CONFLICT (salesman_key) DO UPDATE SET category=EXCLUDED.category""",
            (salesman_key, category),
        )
    return {"salesman_key": salesman_key, "category": category}
