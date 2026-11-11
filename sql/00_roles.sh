#!/bin/sh
# Crea los roles de escritura y de solo lectura antes de aplicar el DDL.
# Se ejecuta solo la primera vez que se inicializa el volumen de PostgreSQL.
set -eu

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v etl_password="${ETL_DB_PASSWORD:?falta ETL_DB_PASSWORD}" \
     -v web_password="${WEB_DB_PASSWORD:?falta WEB_DB_PASSWORD}" <<'EOSQL'
SELECT format('CREATE ROLE etl_rw LOGIN PASSWORD %L', :'etl_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'etl_rw') \gexec
SELECT format('ALTER ROLE etl_rw WITH LOGIN PASSWORD %L', :'etl_password') \gexec

SELECT format('CREATE ROLE web_ro LOGIN PASSWORD %L', :'web_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') \gexec
SELECT format('ALTER ROLE web_ro WITH LOGIN PASSWORD %L', :'web_password') \gexec

ALTER ROLE etl_rw SET search_path = public;
ALTER ROLE web_ro SET search_path = public;
EOSQL

# El DDL se aplica como etl_rw para que sea el dueño de las tablas y
# las default privileges de web_ro apliquen a las tablas futuras.
SQL_FILE="${SQL_DIR:-/sql}/001_schema.sql"
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -c "SET ROLE etl_rw" \
     -f "$SQL_FILE"
