#!/bin/sh
# Scheduler mínimo: comprueba la versión de datos del GDC y relanza el
# pipeline solo cuando cambia. Sin Airflow, sin cron del host.
set -eu

INTERVAL="${SCHEDULER_INTERVAL_SECONDS:-604800}"

echo "scheduler: intervalo de ${INTERVAL}s; primera comprobación inmediata"
while true; do
    echo "scheduler: comprobando data_release del GDC ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))"
    if ! tcga check-release --run-if-changed; then
        echo "scheduler: la comprobación falló; se reintentará en ${INTERVAL}s" >&2
    fi
    sleep "$INTERVAL"
done
