# One-shot database setup for Windows. Run from the project root.
# Prompts for the postgres superuser password.
$ErrorActionPreference = "Stop"

$db = "salesguard"
$user = "salesguard"
$pass = "salesguard"

Write-Host "Creating role and database (UTF8 is required - see README section 2)"
psql -U postgres -v ON_ERROR_STOP=1 -c @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$user') THEN
    CREATE ROLE $user LOGIN PASSWORD '$pass';
  END IF;
END `$`$;
"@
psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $db OWNER $user ENCODING 'UTF8' TEMPLATE template0;"

Write-Host "Applying schema"
$env:PGPASSWORD = $pass
psql -U $user -d $db -v ON_ERROR_STOP=1 -f db/01_schema.sql
psql -U $user -d $db -v ON_ERROR_STOP=1 -f db/02_seed_rules.sql

Write-Host "Done. Next: cd backend; uvicorn app.main:app --reload --port 8000"
