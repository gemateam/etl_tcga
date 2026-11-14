# Despliegue en Render

El ETL, el ejecutor visual `flow` y DuckDB se quedan **en local** (Windows +
Docker Desktop). En Render solo se publica el reporteador `tcga-web` y su base
de datos PostgreSQL.

Este documento describe el despliegue **con los recursos gratuitos** de Render
(`tcga-web` free + `tcga-postgres` free), que es el modo por defecto de
[`render.yaml`](./render.yaml): **0 $/mes**. Al final se explica qué cambia si
algún día se sube a un plan pagado.

```
   PC (Windows + Docker Desktop)                    Render (plan free, 0 $)
   ┌──────────────────────────────┐                ┌────────────────────────────┐
   │ flow + ETL + DuckDB          │                │ tcga-web (Docker, free)    │
   │ data/landing, bronze, ...    │                │   512 MB, /data efímero    │
   │ tcga-postgres 16.9 (local)   │                │ PostgreSQL 18 free (1 GB)  │
   └───────────┬──────────────────┘                └──────────────▲─────────────┘
               │  scripts/render_sync.py                          │
               │  pg_dump ─ pg_restore (URL externa) ─────────────┘
```

| Componente | Dónde | Plan | Persistencia |
|---|---|---|---|
| ETL, `flow`, landing/bronze/silver/gold | PC | — | `data/` + volumen `pgdata` |
| `tcga-postgres` | Render | free | 1 GB (el esquema actual ocupa ~230 MB) |
| `tcga-web` | Render | free | **ninguna**: `/data` se pierde en cada reinicio |

## Qué funciona y qué no en el plan free

Render no ofrece **disco persistente ni SSH** en el plan free, así que los
2,45 GB de Parquet (`silver/case_expressions/`) y de reportes JSON
(`case_reports/`) **no se pueden subir**. La imagen arranca con `/data` vacío.

| Página | Fuente | En el plan free |
|---|---|---|
| Portada, casos, muestras, diagnósticos, exposiciones | PostgreSQL | ✅ |
| Genes, mutaciones (`silver.mutations`, 89.568 filas), `gold.*` | PostgreSQL | ✅ |
| Estado del pipeline (`ops.pipeline_runs`) | PostgreSQL | ✅ |
| GEMAMX (ranking, fármacos, FASTA) | PostgreSQL | ✅ |
| Expresión por gen | DuckDB sobre `/data/silver` | ❌ selector vacío |
| Reporte/PDF por caso | JSON en `/data/case_reports` | ❌ bloque oculto (`case_report = None`) |

No hay errores: `analytics.available()` devuelve `False` y las plantillas están
protegidas con `{% if case_report %}`. Es una **demo docente** de todo el
modelado, sin las dos páginas que dependen de ficheros.

### Límites del plan free

- **La web se duerme a los 15 minutos sin tráfico**. El primer acceso tarda
  ~1 minuto en despertar; luego va normal.
- **PostgreSQL free caduca a los 30 días** desde su creación y Render la
  elimina. Si la base ya tiene ~6 días, quedan ~24: planifica el volcado
  (`--keep-dump`) o vuelve a crearla y resincronizar.
- 1 GB de base de datos: el volcado restaurado ocupa ~230 MB, así que hay
  margen para crecer.
- 512 MB de RAM y sin disco: no intentes ejecutar DuckDB contra Parquet ahí.
- Sin SSH ni trabajos en segundo plano.

## Requisitos

1. Cuenta de Render con el repositorio `gemateam/etl_tcga` conectado (el plan
   free **no** pide método de pago).
2. Docker Desktop en el PC con el contenedor `tcga-postgres` en marcha
   (`docker compose up -d postgres`).
3. Python 3.11+ en el PC para lanzar `scripts/render_sync.py`.
4. Opcional: cliente OpenSSH (`ssh -V`) **solo** si algún día usas el modo
   `files` con un plan pagado.

## 1. Servicios en Render

El Blueprint mantiene los servicios ya creados:

1. Render Dashboard → **New +** → **Blueprint** → repositorio y rama
   (`feat/knime-flow`).
2. Render lee `render.yaml` y muestra `tcga-postgres` (free, PostgreSQL 18) y
   `tcga-web` (Docker free, sin disco).
3. **Apply**. El primer build instala solo dependencias web con `uv`
   (2–4 minutos).
4. Comprueba que no queden servicios antiguos: si el Dashboard muestra
   `tcga-flow` u otros dos servicios suspendidos de intentos anteriores,
   elimínalos para no gastar cuota de proyectos free.

`render.yaml` fija `postgresMajorVersion: "18"` porque la base existente es
18.7; no se puede cambiar después. `scripts/render_sync.py` usa el cliente
`postgres:18`, que restaura sin problema el volcado del PostgreSQL 16.9 local.

## 2. Datos de conexión

Solo hace falta la URL externa de la base:

`tcga-postgres` → **Connections → External Database URL**

Crea `.env.render` en la raíz del repositorio (está en `.gitignore`):

```dotenv
RENDER_DATABASE_URL=postgresql://tcga_admin:CLAVE@dpg-xxxxx-a.oregon-postgres.render.com/tcga
```

`RENDER_SSH_HOST` solo se usa en el modo `files` (plan pagado) y puede quedar
fuera. También puedes pasar la URL con `--database-url` o como variable de
entorno. El script nunca imprime la contraseña.

## 3. Sincronizar la base de datos

```powershell
# Ver la configuracion y las filas locales, sin tocar Render
python scripts/render_sync.py db --dry-run

# Sincronizar de verdad
python scripts/render_sync.py db
```

Opciones:

| Opción | Efecto |
|---|---|
| `--dry-run` | Muestra tamaños y destino sin tocar nada. |
| `-y` | No pide confirmación. |
| `--keep-dump RUTA` | Guarda copia del volcado (recomendado antes de que caduque la BD). |
| `--env-file RUTA` | Otro fichero de entorno (por defecto `.env.render`). |
| `--only ...` / `files` / `all` | Solo tienen sentido con disco (plan pagado). |

Qué hace el modo `db`:

1. `pg_dump --format=custom --no-owner --no-privileges` dentro de
   `tcga-postgres` y `docker cp` al PC (~31 MB).
2. `pg_restore --clean --if-exists --no-owner --no-privileges
   --single-transaction` en un contenedor `postgres:18` contra la URL externa;
   la contraseña viaja por un fichero de entorno temporal, nunca por `argv`.
3. Compara `silver.cases`, `silver.samples`, `silver.mutations`,
   `gold.cx_pivot` y `ops.pipeline_runs`; si no coinciden, termina con error.

`--single-transaction` hace el reemplazo atómico: si algo falla, Render conserva
los datos anteriores. Los esquemas `bronze`, `silver`, `gold` y `ops` del
destino se reemplazan por completo. La operación se validó de extremo a extremo
contra PostgreSQL 18.7 (mismos recuentos y segunda pasada idempotente).

## 4. Verificar

1. `https://tcga-web.onrender.com/health` → `{"status":"ok"}` (el primer acceso
   puede tardar ~1 min: el servicio free está dormido).
2. Portada: casos, muestras, diagnósticos y mutaciones.
3. **Genes / mutaciones**: `TP53`, `PIK3CA`… con sus recuentos.
4. **GEMAMX**: ranking y fármacos.
5. **Expresión** y **reporte por caso**: en free salen vacíos; es lo esperado
   (ver la tabla de arriba).

## 5. Ciclo habitual

```powershell
# En local: actualizar el ETL
docker compose run --rm etl tcga run          # o los nodos que necesites desde flow

# Publicar en Render
python scripts/render_sync.py db
```

- La web no necesita reiniciarse: consulta PostgreSQL en cada petición.
- Comprueba que el `.env` local **no** apunta a Render: si `DATABASE_URL` lleva
  la URL externa, el ETL escribiría en la nube y la siguiente sincronización lo
  sobrescribiría. Lo normal es dejar `DATABASE_URL` vacío y usar los
  contenedores.
- Evita sincronizar mientras alguien consulte: `pg_restore --clean` bloquea las
  tablas que reemplaza.
- Un redespliegue de código reinicia el contenedor y **vacía `/data`** (en free
  ya está vacío, así que no cambia nada).

## Cómo funciona la imagen de la web

`web/Dockerfile.render`:

- Multi-stage con `uv` (solo dependencias de `web/pyproject.toml`; el ETL no se
  instala).
- `USER root` + `web/entrypoint.render.sh`: prepara `/data` (exista o no) y cede
  el proceso a `app` (UID 10001) con `setpriv`. Uvicorn nunca corre como root.
- Crea `~/.ssh` (0700) porque Render lo exige aunque el plan free no permita
  entrar.
- Escucha en `$PORT` (por defecto 10000) y expone `/health`, que también
  comprueba la conexión a PostgreSQL.

## Seguridad y límites

- **La web es pública y no tiene autenticación.** Los datos del TCGA son de
  acceso abierto; si necesitas restringirla, añade HTTP Basic en un proxy o
  protege la URL con Render/Cloudflare. No subas datos sensibles.
- **PostgreSQL acepta conexiones externas** (`ipAllowList 0.0.0.0/0`) para que
  el PC pueda restaurar. Usa una contraseña larga y no la compartas. Puedes
  limitar la lista a tu IP cuando no estés sincronizando.
- **Una sola instancia**: no escales `tcga-web` horizontalmente (el plan free no
  lo permite).
- **Sin `flow` en Render**: el lienzo, el ETL y las descargas del GDC siguen en
  el PC. `flow` conserva su auth Basic opcional (`FLOW_USER`/`FLOW_PASSWORD`)
  por si se publica en el futuro.
- **Caducidad**: anota la fecha de creación de `tcga-postgres`; a los 30 días
  Render la borra.

## Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| `Falta RENDER_DATABASE_URL` | Crea `.env.render` o exporta la variable. |
| El primer acceso tarda ~1 min | Servicio free dormido; es normal. |
| `pg_restore: error: connection ... SSL` | Añade `--sslmode require` (por defecto) o prueba `RENDER_PG_SSLMODE=prefer`. |
| `pg_restore: error: ... is being accessed by other users` | Hay consultas activas en la web; reintenta en unos segundos. |
| `Host key verification failed` | Solo afecta a `files`; el script usa `accept-new`. |
| `No hay espacio suficiente` | La base free tiene 1 GB; revisa `pg_database_size`. |
| La página de expresión está vacía | Esperado en free: no hay Parquet en `/data`. |
| El reporte por caso no muestra datos | Esperado en free: no hay `case_reports` en `/data`. |
| `Faltan rutas locales: ...` | El ETL no ha generado esa salida en `data/`. |
| Recuentos distintos tras restaurar | Revisa el log de `pg_restore`; la transacción se revirtió y Render conserva lo anterior. |
| La base desapareció | Han pasado 30 días desde su creación (plan free). |

## Subir a un plan pagado (web completa)

Si necesitas expresión y reportes por caso en Render hacen falta disco y SSH.
La combinación mínima razonable:

| Recurso | Plan | Coste |
|---|---|---|
| `tcga-web` | `0.5c-512mb` + disco 4 GB | 7 $ + 1 $ |
| `tcga-postgres` | `0.1c-256mb` | 6 $ |
| **Total** | | **~14 $/mes** |

Cambios a hacer entonces:

1. En `render.yaml`, `tcga-web`: `plan: starter` y el bloque `disk`
   (`name: tcga-data`, `mountPath: /data`, `sizeGB: 4`).
2. En `tcga-postgres`: `plan: basic-256mb` (la versión 18 no cambia).
3. Añadir `RENDER_SSH_HOST` a `.env.render` y ejecutar
   `python scripts/render_sync.py all` (sube ~2,45 GB por `tar | ssh`).

El modo `files` del script ya está implementado y probado; en free simplemente
no se usa.
