"""Excel ingest for the FMCG company's 'Outstanding with Outlet' report.

The report is not a clean table: 8 preamble rows (report period, filters),
then the header row, then the data. We locate the header by content, never by
position, so a changed preamble does not break the load.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .db import conn

# source header -> our column name
COLUMN_MAP = {
    "customer code": "customer_code",
    "customer name": "customer_name",
    "channel type": "channel_type",
    "outlet type": "outlet_type",
    "loyalty program": "loyalty_program",
    "credit term": "credit_term",
    "salesman name": "salesman_name",
    "beat": "beat",
    "salesman type": "salesman_type",
    "document type": "document_type",
    "document no.": "document_no",
    "document no": "document_no",
    "document date": "document_date",
    "due days": "due_days",
    "over due days": "overdue_days",
    "overdue days": "overdue_days",
    "invoice status": "invoice_status",
    "rule book": "rule_book",
}

REQUIRED = {"customer_code", "document_type", "document_no"}

# Beat names encode the area. Extend this table as Avensons confirms Q7.
LOCATION_PATTERNS = [
    (r"\bISS[_ ]", "ISS_TIRUPPUR"),
    (r"\bPACE", "CBE_PACE"),
    (r"\bMT\b|MODERN", "MODERN_TRADE"),
    (r"\bDIGITAL", "DIGITAL"),
    (r"\bKEY\s?AC", "KEY_ACCOUNTS"),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _find_header_row(path: Path, sheet) -> int:
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=40, dtype=str)
    for idx, row in probe.iterrows():
        cells = {_norm(v) for v in row.tolist() if pd.notna(v)}
        if "customer code" in cells and any(c.startswith("document type") for c in cells):
            return int(idx)
    raise ValueError("Header row not found - is this the Outstanding with Outlet report?")


def _printed_at(path: Path, sheet) -> datetime | None:
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=3, dtype=str)
    for v in probe.fillna("").values.ravel():
        m = re.search(r"at\s+(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})", str(v))
        if m:
            return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S")
    return None


def _money(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^0-9eE.\-+]", "", regex=True).replace("", None),
        errors="coerce",
    ).fillna(0)


def read_outstanding(path: str | Path, sheet: int | str = 0) -> tuple[pd.DataFrame, datetime | None]:
    path = Path(path)
    header_row = _find_header_row(path, sheet)
    raw = pd.read_excel(path, sheet_name=sheet, header=header_row)
    raw = raw.rename(columns={c: COLUMN_MAP.get(_norm(c).rstrip(" (?)₹"), _norm(c)) for c in raw.columns})

    # the amount/balance headers carry a currency symbol that Excel mangles
    for col in list(raw.columns):
        if col.startswith("amount"):
            raw = raw.rename(columns={col: "amount"})
        elif col.startswith("balance"):
            raw = raw.rename(columns={col: "balance"})

    missing = REQUIRED - set(raw.columns)
    if missing:
        raise ValueError(f"Missing expected columns: {sorted(missing)}")

    df = raw.dropna(subset=["customer_code", "document_no"]).copy()
    df["customer_code"] = df["customer_code"].astype(str).str.strip()
    df["document_no"] = df["document_no"].astype(str).str.strip()
    df["document_type"] = df["document_type"].astype(str).str.strip().str.upper()
    df["document_date"] = pd.to_datetime(df["document_date"], errors="coerce").dt.date
    df["amount"] = _money(df["amount"])
    df["balance"] = _money(df["balance"])
    for col in ("due_days", "overdue_days", "credit_term"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    for col in ("customer_name", "channel_type", "outlet_type", "loyalty_program",
                "salesman_name", "beat", "salesman_type", "invoice_status", "rule_book"):
        df[col] = df.get(col).astype("string").str.strip()
    df["source_ignored"] = df["rule_book"].fillna("").str.upper().eq("IGNORE")

    # one source row per (customer, type, no) - the sheet occasionally repeats
    df = df.drop_duplicates(subset=["customer_code", "document_type", "document_no"], keep="last")
    return df, _printed_at(path, sheet)


def _split_beat(raw_beat: str | None) -> tuple[str | None, str | None, str | None]:
    if not raw_beat or pd.isna(raw_beat):
        return None, None, None
    parts = str(raw_beat).split("-", 1)
    code = parts[0].strip() if len(parts) == 2 else None
    name = (parts[1] if len(parts) == 2 else parts[0]).strip()
    location = "CBE"
    for pattern, loc in LOCATION_PATTERNS:
        if re.search(pattern, name.upper()):
            location = loc
            break
    return code, name, location


def load_snapshot(
    path: str | Path,
    snapshot_date: date | None = None,
    snapshot_type: str = "daily",
    replace: bool = True,
    derive_monthly_sales: bool = True,
) -> dict:
    """Load one outstanding file into the warehouse. Idempotent per (date, type)."""
    df, printed_at = read_outstanding(path)
    snapshot_date = snapshot_date or (printed_at.date() if printed_at else date.today())

    with conn() as c, c.cursor() as cur:
        if replace:
            cur.execute(
                "DELETE FROM snapshot WHERE snapshot_date = %s AND snapshot_type = %s",
                (snapshot_date, snapshot_type),
            )
        cur.execute(
            """INSERT INTO snapshot (snapshot_date, snapshot_type, source_file, printed_at, row_count)
               VALUES (%s,%s,%s,%s,%s) RETURNING snapshot_id""",
            (snapshot_date, snapshot_type, Path(path).name, printed_at, len(df)),
        )
        snapshot_id = cur.fetchone()["snapshot_id"]

        # ---- salesmen
        smen = (
            df.dropna(subset=["salesman_name"])
            .groupby("salesman_name", dropna=True)["salesman_type"]
            .agg(lambda s: s.dropna().iloc[0] if s.notna().any() else None)
        )
        cur.executemany(
            """INSERT INTO dim_salesman (salesman_name, salesman_type) VALUES (%s,%s)
               ON CONFLICT (salesman_name) DO UPDATE
                 SET salesman_type = COALESCE(EXCLUDED.salesman_type, dim_salesman.salesman_type)""",
            list(smen.items()),
        )
        cur.execute("SELECT salesman_name, salesman_key FROM dim_salesman")
        sman_keys = {r["salesman_name"]: r["salesman_key"] for r in cur.fetchall()}

        # ---- beats
        beats = [b for b in df["beat"].dropna().unique()]
        cur.executemany(
            """INSERT INTO dim_beat (raw_beat, beat_code, beat_name, location)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (raw_beat) DO UPDATE
                 SET beat_code = EXCLUDED.beat_code, beat_name = EXCLUDED.beat_name""",
            [(b, *_split_beat(b)) for b in beats],
        )
        cur.execute("SELECT raw_beat, beat_key FROM dim_beat")
        beat_keys = {r["raw_beat"]: r["beat_key"] for r in cur.fetchall()}

        # ---- customers (latest attributes win, first/last seen tracked)
        cust = df.groupby("customer_code").agg(
            customer_name=("customer_name", "last"),
            channel_type=("channel_type", "last"),
            outlet_type=("outlet_type", "last"),
            loyalty_program=("loyalty_program", "last"),
            credit_term=("credit_term", "max"),
            first_doc=("document_date", "min"),
            last_doc=("document_date", "max"),
        ).reset_index()
        cur.executemany(
            """INSERT INTO dim_customer (customer_code, customer_name, channel_type, outlet_type,
                                         loyalty_program, credit_term, first_seen_on, last_seen_on)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (customer_code) DO UPDATE SET
                 customer_name = EXCLUDED.customer_name,
                 channel_type  = EXCLUDED.channel_type,
                 outlet_type   = EXCLUDED.outlet_type,
                 loyalty_program = EXCLUDED.loyalty_program,
                 credit_term   = EXCLUDED.credit_term,
                 first_seen_on = LEAST(dim_customer.first_seen_on, EXCLUDED.first_seen_on),
                 last_seen_on  = GREATEST(dim_customer.last_seen_on, EXCLUDED.last_seen_on)""",
            [
                (r.customer_code, _s(r.customer_name), _s(r.channel_type), _s(r.outlet_type),
                 _s(r.loyalty_program), int(r.credit_term or 0), r.first_doc, r.last_doc)
                for r in cust.itertuples()
            ],
        )

        # ---- documents
        rows = [
            (
                snapshot_id, r.customer_code,
                sman_keys.get(r.salesman_name) if pd.notna(r.salesman_name) else None,
                beat_keys.get(r.beat) if pd.notna(r.beat) else None,
                r.document_type, r.document_no, r.document_date,
                float(r.amount), float(r.balance),
                None if pd.isna(r.due_days) else int(r.due_days),
                None if pd.isna(r.overdue_days) else int(r.overdue_days),
                _s(r.invoice_status), bool(r.source_ignored),
            )
            for r in df.itertuples()
        ]
        cur.executemany(
            """INSERT INTO outstanding_document
                 (snapshot_id, customer_code, salesman_key, beat_key, document_type, document_no,
                  document_date, amount, balance, due_days, overdue_days, invoice_status, source_ignored)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (snapshot_id, customer_code, document_type, document_no) DO NOTHING""",
            rows,
        )

        # PP beats tolerate more due days - flag them on the master
        cur.execute(
            "UPDATE dim_beat SET due_tolerance_days = 0 WHERE due_tolerance_days IS NULL"
        )

        result = {
            "snapshot_id": snapshot_id,
            "snapshot_date": str(snapshot_date),
            "snapshot_type": snapshot_type,
            "documents": len(rows),
            "customers": len(cust),
            "salesmen": len(smen),
            "beats": len(beats),
            "printed_at": str(printed_at) if printed_at else None,
        }

        if derive_monthly_sales:
            result["monthly_sales_rows"] = _derive_monthly_sales(cur, snapshot_id)

    return result


def _s(v):
    return None if v is None or pd.isna(v) else str(v)


def _derive_monthly_sales(cur, snapshot_id: int) -> int:
    """Fallback sales history.

    Real monthly sales (shop-wise, salesman-wise) come from a separate upload.
    Until Avensons sends it, we approximate it from invoice history inside the
    outstanding file so the dumping / drop rules are testable. Rows are marked
    source='derived' and are replaced the moment a real file lands.
    """
    cur.execute(
        """
        INSERT INTO monthly_sales (month_start, customer_code, salesman_key, category,
                                   invoice_count, total_amount, source)
        SELECT date_trunc('month', d.document_date)::date, d.customer_code, d.salesman_key,
               NULL, count(*), sum(d.amount), 'derived'
        FROM outstanding_document d
        WHERE d.snapshot_id = %s AND d.document_type = 'INV'
          AND d.salesman_key IS NOT NULL AND d.document_date IS NOT NULL
        GROUP BY 1,2,3
        ON CONFLICT (month_start, customer_code, salesman_key) DO UPDATE
          SET invoice_count = EXCLUDED.invoice_count,
              total_amount  = EXCLUDED.total_amount,
              source        = 'derived'
          WHERE monthly_sales.source = 'derived'
        """,
        (snapshot_id,),
    )
    return cur.rowcount
