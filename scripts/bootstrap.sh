#!/usr/bin/env bash
# Avensons SalesGuard - one-shot setup for Ubuntu / WSL.
#
#   ./scripts/bootstrap.sh /path/to/Outstanding_with_Outlet__FOR_AV.xlsx
#
# Installs nothing globally except PostgreSQL and Node (via apt). Everything
# Python lives in ./backend/.venv. Safe to re-run.
set -euo pipefail

EXCEL="${1:-}"
DB=salesguard
DBUSER=salesguard
DBPASS=salesguard
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- 1. packages
say "Installing PostgreSQL, Python venv and Node"
sudo apt-get update -qq
sudo apt-get install -y postgresql postgresql-client python3-venv python3-pip nodejs npm
sudo systemctl enable --now postgresql

# ---------------------------------------------------------------- 2. database
say "Creating role '$DBUSER' and database '$DB' (UTF8)"
sudo -u postgres psql -qv ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DBUSER') THEN
    CREATE ROLE $DBUSER LOGIN PASSWORD '$DBPASS';
  END IF;
END \$\$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
  # UTF8 is required: a SQL_ASCII database makes psycopg return bytes instead of
  # str, which silently breaks every salesman and beat lookup during ingest.
  sudo -u postgres createdb -O "$DBUSER" -E UTF8 -T template0 "$DB"
fi

export PGPASSWORD="$DBPASS"
PSQL="psql -h localhost -U $DBUSER -d $DB -qv ON_ERROR_STOP=1"

say "Applying schema and rule catalogue"
$PSQL -f db/01_schema.sql
$PSQL -f db/02_seed_rules.sql

# ---------------------------------------------------------------- 3. backend
say "Setting up the Python environment"
python3 -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements.txt
[ -f backend/.env ] || cp backend/.env.example backend/.env

# ---------------------------------------------------------------- 4. first load
if [ -n "$EXCEL" ]; then
  if [ ! -f "$EXCEL" ]; then
    echo "Excel file not found: $EXCEL" >&2
    exit 1
  fi
  say "Loading $(basename "$EXCEL") and running the rules"
  backend/.venv/bin/python scripts/load_and_run.py "$EXCEL"

  # dim_salesman is only populated by the load above, so the placeholder
  # category mapping has to come afterwards to have anything to map.
  say "Applying the placeholder category mapping (remove once Q5/Q6 are answered)"
  $PSQL -f db/03_demo_mapping.sql
  backend/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.db import open_pool, fetch_one
from app.rules import run_rules
open_pool()
snap = fetch_one("SELECT snapshot_id FROM sg.snapshot ORDER BY snapshot_id DESC LIMIT 1")
run_rules(snap["snapshot_id"])
print("Rules re-run with the category mapping in place.")
PY
else
  say "No Excel given - skipping the first load"
  echo "    Load one later with:"
  echo "    backend/.venv/bin/python scripts/load_and_run.py /path/to/report.xlsx"
fi

# ---------------------------------------------------------------- 5. frontend
say "Installing the frontend"
(cd frontend && npm install --no-fund --no-audit)

cat <<'DONE'

==> Setup complete. Start the two servers in separate terminals:

  Terminal 1 (API on :8000)
    cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

  Terminal 2 (UI on :5173)
    cd frontend && npm run dev

  Then open http://localhost:5173

DONE
