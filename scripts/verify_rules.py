"""Independent verification: recompute a few rules in pandas and diff against the DB.

Usage: python scripts/verify_rules.py /path/to/outstanding.xlsx
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import open_pool, fetch_all
from app.ingest import read_outstanding

XL = sys.argv[1] if len(sys.argv) > 1 else "Outstanding_with_Outlet__FOR_AV.xlsx"
open_pool()
df, _ = read_outstanding(XL)
df["sman"] = df["salesman_name"].fillna("")
cig = df["sman"].str.upper().str.startswith("TH") | df["sman"].str.upper().isin(
    ["MANIKANDAN VAN", "NANDAKUMAR VAN"])
df["eff"] = df["overdue_days"].fillna(df["due_days"]).fillna(0)
scope = df[(~cig) & (df["balance"].abs() > 10)]

print(f"scope rows            pandas={len(scope):>6}  db={fetch_all('select count(*) n from sg.f_scope(1)')[0]['n']:>6}")
print(f"cig excluded          pandas={int(cig.sum()):>6}  source RULE BOOK=IGNORE={int(df['source_ignored'].sum()):>6}"
      f"  agreement={float((cig == df['source_ignored']).mean()):.4f}")

r09 = scope[(scope.document_type.isin(["INV", "OPN", "DBN"])) & (scope.balance > 10) & (scope.eff > 30)]
db09 = fetch_all("select count(*) n, round(sum(balance)) e from sg.rule_violation where rule_code='R09_OVER_30_DAY_INVOICE'")[0]
print(f"R09 over-30-day       pandas={len(r09):>6} Rs{r09.balance.sum():>12,.0f}   db={db09['n']:>6} Rs{db09['e']:>12,.0f}")

g = scope[(scope.document_type != "CRN") & (scope.balance > 10)].groupby(["customer_code", "sman"])
pairs = g.agg(n=("balance", "size"), worst=("eff", "max"))
r02 = pairs[(pairs.n >= 2) & (pairs.worst <= 30)]
db02 = fetch_all("""select count(*) n, count(distinct (customer_code, salesman_key)) pairs
                    from sg.rule_violation where rule_code='R02_SAME_SALESMAN_DOUBLE_BILL'""")[0]
print(f"R02 double bill       pandas pairs={len(r02):>4} bills={int(r02.n.sum()):>4}"
      f"   db pairs={db02['pairs']:>4} bills={db02['n']:>4}")

r07 = scope[(scope.document_type == "INV") & (scope.amount >= 500) & (scope.balance > 0)
            & (scope.balance <= scope.amount * 0.10)]
db07 = fetch_all("select count(*) n from sg.rule_violation where rule_code='R07_LOW_OUTSTANDING_RATIO'")[0]
print(f"R07 low outstanding   pandas={len(r07):>6}   db={db07['n']:>6}")

r01 = scope[((scope.document_type == "DBN") | scope.document_no.str.upper().str.startswith("DR"))
            & (scope.balance > 10) & (scope.eff >= 14)]
db01 = fetch_all("select count(*) n from sg.rule_violation where rule_code='R01_DBN_OVERDUE'")[0]
print(f"R01 DBN/DR overdue    pandas={len(r01):>6}   db={db01['n']:>6}")
