# Avensons SalesGuard

Decision-support tool over the FMCG company's daily "Outstanding with Outlet"
Excel report. Loads the sheet, applies the Avensons rule book, and produces
customer-wise and salesman-wise lists with a reason on every line.

Built and tested against `Outstanding_with_Outlet__FOR_AV.xlsx`
(29,510 rows, as on 03-09-2026) on PostgreSQL 16.

```
db/          schema, rule catalogue, placeholder category mapping
backend/     FastAPI + psycopg3. Ingest, rule engine, REST API
frontend/    React + Vite, no UI framework
scripts/     CLI loader and a verification harness
```

## Quick start (Ubuntu / WSL)

One script does the lot — packages, database, schema, Python environment,
first load, and npm install:

```bash
unzip avensons-salesguard.zip
cd avensons-salesguard
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh ~/Downloads/Outstanding_with_Outlet__FOR_AV.xlsx
```

Then two terminals:

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000   # API
cd frontend && npm run dev                                          # UI on :5173
```

Sections 1–6 below are the same steps by hand, and are what you want on Windows.

---

## 1. Install PostgreSQL

### Windows

```powershell
winget install PostgreSQL.PostgreSQL.16
# or download the EDB installer from postgresql.org/download/windows

# add psql to this shell's PATH (adjust the version folder if different)
$env:Path += ";C:\Program Files\PostgreSQL\16\bin"
```

### Ubuntu / WSL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client
sudo systemctl enable --now postgresql
```

### macOS

```bash
brew install postgresql@16
brew services start postgresql@16
```

## 2. Create the role and database

**The `ENCODING 'UTF8'` is not optional.** A `SQL_ASCII` database makes psycopg
return `bytes` instead of `str` for every text column, which silently breaks the
salesman and beat lookups during ingest. This bit me during development.

```bash
# Linux/macOS: run as the postgres superuser
sudo -u postgres psql
```

```powershell
# Windows: psql prompts for the password you set during install
psql -U postgres
```

Then, at the `postgres=#` prompt:

```sql
CREATE ROLE salesguard LOGIN PASSWORD 'salesguard';
CREATE DATABASE salesguard OWNER salesguard ENCODING 'UTF8' TEMPLATE template0;
\q
```

## 3. Create the schema

```bash
cd avensons-salesguard

psql -U salesguard -d salesguard -v ON_ERROR_STOP=1 -f db/01_schema.sql
psql -U salesguard -d salesguard -v ON_ERROR_STOP=1 -f db/02_seed_rules.sql

```

Verify:

```bash
psql -U salesguard -d salesguard -c "\dt sg.*"
psql -U salesguard -d salesguard -c "select rule_code, title from sg.rule order by sort_order;"
```

## 4. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
# edit .env if your password or port differs

uvicorn app.main:app --reload --port 8000
```

Check: <http://localhost:8000/health> and <http://localhost:8000/docs>

## 5. Load the first file

Either from the browser (Uploads screen) or from the command line:

```bash
# from the project root, with the venv active
python scripts/load_and_run.py "/path/to/Outstanding_with_Outlet__FOR_AV.xlsx"
```

Salesmen, beats and outlets are created from the file itself, so the master
tables fill up on this first load. **Now** — not before — apply the placeholder
category mapping, if you want to see the category-coverage rule produce output
before Q5/Q6 are answered:

```bash
psql -U salesguard -d salesguard -f db/03_demo_mapping.sql
python scripts/load_and_run.py "/path/to/Outstanding_with_Outlet__FOR_AV.xlsx"
```

The as-on date is read from the report's own print header, so you do not have to
pass it. `--date 2026-09-03` overrides it; `--type monthly` marks it as a sales
file. Loading the same date twice replaces that day's data rather than
duplicating it.

## 6. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to port 8000
```

For a single-machine deployment, `npm run build` and serve `dist/` from any
static host (or IIS/nginx) pointed at the same box as the API.

---

## How it works

**One scope, then rules.** `sg.f_scope()` is a Postgres function that applies the
global filters *once*: small-bill ignore (balance ≤ ₹10), cigarette exclusion,
PP-beat tolerance, and the overdue-days fallback. Each run materialises it into a
temp table, and every rule is a single
`INSERT INTO rule_violation … SELECT … FROM scope`. A rule therefore cannot
forget a global filter, and the whole rule book runs in about half a second on
your 29k-row file.

**Nothing is hard-coded.** Thresholds live in `sg.rule.params` (jsonb) and
`sg.global_config`, both editable from the Rule settings screen. Re-running the
check picks up the new values — no deployment, no code change. Verified: moving
R09 from 30 to 45 days took the list from 2,045 to 1,120 lines.

**Exceptions are data.** `sg.rule_exception` holds customer/salesman pairs that a
given rule should skip. This is how 5.10 Multiple-Billing Exception works, and it
applies to every rule, not just the double-bill one. Verified: excepting one pair
removed exactly its 13 flags and left the other 462 untouched.

**Every run is kept.** `rule_run`, `rule_run_detail` and `rule_violation` are
append-only per run, so you can compare today's list with last Tuesday's and show
a salesman what changed. `rule_violation` grows by roughly the flag count per run;
at a few thousand rows a day this is years of headroom, but if you ever want to
trim it:

```sql
DELETE FROM sg.rule_run WHERE started_at < now() - interval '6 months';
```

### Adding a rule

1. Add one `Rule(...)` entry to `backend/app/rules/engine.py` — a single
   `INSERT … SELECT … FROM scope` statement.
2. Add one seed row to `db/02_seed_rules.sql` with its default parameters.

Nothing else. The API, the sidebar, the settings screen, and the CSV export pick
it up automatically.

---

## Rule coverage

| Code | Requirement doc | Status |
|---|---|---|
| R01 | 5.1 DBN overdue (+ Sheet1 rule 4: `DR`-prefixed documents over 14 days) | implemented |
| R02 | 5.2 Same-salesman double bill | implemented |
| R03 | 5.3 Cross-salesman credit flag (flags, does not block) | implemented |
| R04 | 5.4 Stock-dumping alert | implemented, wants real sales data |
| R05 | 5.5 Underperforming salesman, 10-day window | implemented |
| R06 | 5.6 Lost potential sale, 10-day window | implemented, wants the Q5 mapping |
| R07 | 5.7 Low outstanding ratio | implemented |
| R08 | 5.8 Chronic default customer | implemented |
| R09 | 5.9 Over-30-day invoice list | implemented |
| — | 5.10 Multiple-billing exception | `rule_exception` table |
| — | 5.11 Small-bill ignore | global filter in `f_scope` |
| — | 5.12 Cigarette exclusion | global filter in `f_scope` |
| R14 | Sheet1 rule 6: 80–100% uncollected after 30 days | implemented (not in the PDF) |
| R15 | 5.13 Dormant customer | implemented |
| R16 | 5.13 Sudden drop in sales | implemented, wants real sales data |
| R17 | 5.13 Beat coverage gap | implemented |
| R18 | 5.13 New customer risk | implemented, skipped until 2+ snapshots exist |

High Return Rate and Frequent Small Payments are not built — the PDF marks the
first "LEAVE IT NOW", and the second needs collection entries the outstanding
report does not carry.

---

## What the data settled

**Q4, Due Days vs Over Due days.** `Due Days` is the plain calendar age of the
document: as-on-date minus document date, matching on 100% of the 29,388 rows
that have both columns. `Over Due days` is that age minus the credit grace, and
it is **empty on every DBN and OPN row** (922 of them). So the rules use
*Over Due days*, falling back to *Due Days* when it is null — otherwise every
bounced cheque would silently drop out of the 30-day list.

**Q1, which average for stock dumping.** Option A, the monthly average. Option B
divides by invoice count, so a shop billed weekly gets a small base and ordinary
bills trip the rule. Both are available behind the `average_basis` parameter.

**5.12 cigarette exclusion, confirmed against your own marking.** Implementing it
from the logic in the PDF (Manikandan Van, Nandakumar Van, or a salesman name
starting "TH") reproduced the `RULE BOOK = IGNORE` column in your sheet exactly:
20,134 of 20,134 rows, no disagreement either way. After that and the ₹10 filter,
8,754 of 29,510 documents remain in scope.

**Sheet1 carried rules the requirement document missed** — the `DR`-prefix
14-day rule, the 80–100%-uncollected rule, and the ≤10%-outstanding rule. The
first two are now R01 and R14.

**Beat tolerance.** Only 46 of 302 beats end in "PP", so the tolerance is a
narrow carve-out. It is a parameter (`pp_tolerance_days`, applied to
`Grocery 2 Ds` salesmen) defaulted to **0** until Avensons says how many extra
days are actually tolerated.

## Still open

- **Q5/Q6.** The four product groups and the salesman→group mapping. R06 is
  currently running off a placeholder derived from `Salesman Type`, so its output
  is illustrative only.
- **Monthly sales, shop-wise and salesman-wise.** Until it arrives, the loader
  derives a stand-in from invoice history inside the outstanding file. Those rows
  are marked `source='derived'` and are replaced by the first real upload. R04
  and R16 carry a warning in the UI because of it.
- **Q7 beat/location.** `dim_beat.location` is derived from name patterns
  (`ISS_`, `PACE_`, `KEY AC`, Digital) and defaults to CBE. It is a plain
  editable column — correct it once and the cigarette filter narrows properly.
- **Q11 salesperson access.** The schema has `app_user` with roles and
  `violation_note` for salesperson explanations, but Phase 1 ships no login.

## Verifying a load yourself

`scripts/verify_rules.py` recomputes several rules in pandas, straight from the
Excel, and diffs them against what Postgres produced. Useful whenever a threshold
changes or the source report changes shape.

```
scope rows            pandas=  8754  db=  8754
cig excluded          pandas= 20134  source RULE BOOK=IGNORE= 20134  agreement=1.0000
R09 over-30-day       pandas=  2045 Rs  97,003,265   db=  2045 Rs  97,003,265
R02 double bill       pandas pairs= 200 bills= 475   db pairs= 200 bills= 475
R07 low outstanding   pandas=   152   db=   152
R01 DBN/DR overdue    pandas=    31   db=    31
```
# Avensons-Data-Project
