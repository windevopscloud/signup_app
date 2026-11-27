#!/bin/bash
# Runs on the tools EC2 (has psql, IAM role). Creates or syncs the app DB user so its
# password matches the current version of the signup/poc/rds-app secret. Safe to repeat.
set -euo pipefail
export HOME=/root
REGION="${AWS_REGION:-us-west-2}"
APP_SECRET="${APP_SECRET:-signup/poc/rds-app}"

get() { aws secretsmanager get-secret-value --region "$REGION" --secret-id "$1" --query SecretString --output text; }

APP_JSON=$(get "$APP_SECRET")
j() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['$2'])"; }

HOST=$(j "$APP_JSON" host); PORT=$(j "$APP_JSON" port); DB=$(j "$APP_JSON" dbname)
APP_USER=$(j "$APP_JSON" username); APP_PW=$(j "$APP_JSON" password)
MASTER_JSON=$(get "$(j "$APP_JSON" masterarn)")
export PGPASSWORD=$(j "$MASTER_JSON" password)
MASTER_USER=$(j "$MASTER_JSON" username)

echo "Syncing database user ${APP_USER} on ${HOST}"
psql "host=${HOST} port=${PORT} dbname=${DB} user=${MASTER_USER} sslmode=require" \
  -v ON_ERROR_STOP=1 -v app_user="$APP_USER" -v app_pw="$APP_PW" -v dbname="$DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_pw') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'dbname', :'app_user') \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_user') \gexec
SQL
echo "Database user ready"
