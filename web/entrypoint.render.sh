#!/bin/sh
# Entrypoint de la imagen del reporteador en Render.
#
# El disco persistente se monta en /data y la primera vez pertenece a root.
# Arrancamos como root solo para ajustar el propietario y despues cedemos el
# proceso a `app` (UID 10001), que es quien ejecuta uvicorn.
#
# El chown recursivo solo ocurre cuando /data no es de `app`, es decir, la
# primera vez que se monta el disco o despues de subir datos como root.
# Es una operacion de metadatos: sobre los ~2,5 GB actuales tarda segundos.
set -e

DATA_DIR="${DATA_DIR:-/data}"
APP_UID=10001
APP_GID=10001

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    if [ "$(stat -c %u "$DATA_DIR" 2>/dev/null || echo 0)" != "$APP_UID" ]; then
        chown -R "$APP_UID:$APP_GID" "$DATA_DIR" 2>/dev/null || true
    fi
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups "$@"
fi

exec "$@"
