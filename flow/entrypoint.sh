#!/bin/sh
# Entrypoint del ejecutor visual.
#
# - Siembra los workflows versionados en el directorio de trabajo (disco
#   persistente en Render) sin sobrescribir los cambios hechos desde la UI.
# - Si arranca como root (necesario para preparar un disco persistente nuevo),
#   ajusta permisos y baja privilegios al usuario `app` antes de lanzar uvicorn.
set -e

DATA_DIR="${DATA_DIR:-/data}"
FLOW_WORKFLOWS_DIR="${FLOW_WORKFLOWS_DIR:-/app/workflows}"
FLOW_WORKFLOWS_SEED="${FLOW_WORKFLOWS_SEED:-/app/workflows-seed}"

seed_workflows() {
    [ -d "$FLOW_WORKFLOWS_SEED" ] || return 0
    mkdir -p "$FLOW_WORKFLOWS_DIR"
    for seed in "$FLOW_WORKFLOWS_SEED"/*.json; do
        [ -f "$seed" ] || continue
        target="$FLOW_WORKFLOWS_DIR/$(basename "$seed")"
        if [ ! -f "$target" ]; then
            cp "$seed" "$target"
        fi
    done
}

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR" "$FLOW_WORKFLOWS_DIR"
    # Un disco nuevo llega propiedad de root; se ajusta una única vez.
    if [ "$(stat -c %u "$DATA_DIR" 2>/dev/null || echo 0)" != "$(id -u app)" ]; then
        chown -R app:app "$DATA_DIR" 2>/dev/null || true
    fi
    seed_workflows
    chown app:app "$DATA_DIR" 2>/dev/null || true
    chown app:app "$(dirname "$FLOW_WORKFLOWS_DIR")" 2>/dev/null || true
    chown -R app:app "$FLOW_WORKFLOWS_DIR" 2>/dev/null || true
    exec setpriv --reuid="$(id -u app)" --regid="$(id -g app)" --init-groups "$@"
fi

seed_workflows
exec "$@"
