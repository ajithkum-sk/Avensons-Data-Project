#!/usr/bin/env bash
# One-shot database setup for Linux/macOS. Run from the project root.
set -euo pipefail

DB=${PGDATABASE:-salesguard}
USER=${PGUSER:-salesguard}
PASS=${PGPASSWORD:-salesguard}
SUPER=${SUPERUSER:-postgres}

echo "Creating role and database (UTF8 is required - see README section 2)"
sudo -u "$SUPER" psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$USER') THEN
    CREATE ROLE $USER LOGIN PASSWORD '$PASS';
  END IF;
END \$\$;
SQL
sudo -u "$SUPER" psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" \
  | grep -q 1 || sudo -u "$SUPER" createdb -O "$USER" -E UTF8 -T template0 "$DB"

echo "Applying schema"
psql -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f db/01_schema.sql
psql -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -f db/02_seed_rules.sql

echo "Done. Next: cd backend && uvicorn app.main:app --reload --port 8000"
