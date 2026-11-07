# Despliegue en Render

Este proyecto está configurado para desplegarse en **Render** (https://render.com) usando el Blueprint `render.yaml`.

## Arquitectura en Render

| Componente | Tipo Render | Notas |
|------------|-------------|-------|
| `postgres` | **PostgreSQL administrado** | Base de datos con 1 GB RAM / 1 GB disco (plan gratis) |
| `web` | **Web Service** | FastAPI + Jinja2 + HTMX + Plotly en puerto dinámico `$PORT` |
| `etl` | **Worker** | Job manual para ejecutar el pipeline completo una vez |
| `scheduler` | *No desplegar* | En Render, el ETL se ejecuta manualmente |
| `adminer` | *No desplegar* | Usar pgAdmin/Adminer localmente |

## Pasos para desplegar

### Opción A: Blueprint automático (recomendado)
1. Sube el repositorio a GitHub (gemateam/etl_tcga)
2. Ve a [Render Dashboard](https://dashboard.render.com/)
3. Haz clic en **"New +"** → **"Blueprint"**
4. Conecta el repositorio GitHub `gemateam/etl_tcga`
5. Render detectará `render.yaml` y mostrará los 3 servicios:
   - `tcga-postgres` (PostgreSQL)
   - `tcga-web` (Web Service)
   - `tcga-etl` (Worker)
6. Haz clic en **"Apply"**
7. Render creará los servicios (5-10 minutos)

### Opción B: Crear servicios manualmente
Si el Blueprint falla, crea los servicios manualmente:

#### 1. PostgreSQL
- **Tipo:** PostgreSQL
- **Nombre:** `tcga-postgres`
- **Plan:** Free
- **Database:** `tcga`
- **User:** `tcga_admin`
- La contraseña se genera automáticamente

#### 2. Web Service
- **Tipo:** Web Service
- **Nombre:** `tcga-web`
- **Plan:** Free
- **Region:** Oregon (o más cercana)
- **Branch:** `master`
- **Root Directory:** *(dejar vacío)*
- **Build Command:** *(dejar vacío)*
- **Start Command:** `/app/start.sh`
- **Dockerfile Path:** `./web/Dockerfile.render`

**Variables de entorno:**
- `POSTGRES_HOST` → `tcga-postgres` (host del PostgreSQL)
- `POSTGRES_PORT` → `5432`
- `POSTGRES_DB` → `tcga`
- `POSTGRES_USER` → `tcga_admin`
- `POSTGRES_PASSWORD` → *(secret de la base de datos)*
- `DUCKDB_MEMORY_LIMIT` → `512MB`
- `DUCKDB_THREADS` → `2`
- `GDC_PROJECTS` → `TCGA-BRCA`
- `LOG_LEVEL` → `INFO`

#### 3. Worker (ETL)
- **Tipo:** Worker
- **Nombre:** `tcga-etl`
- **Plan:** Free
- **Region:** Oregon
- **Branch:** `master`
- **Root Directory:** *(dejar vacío)*
- **Build Command:** *(dejar vacío)*
- **Start Command:** `/app/entrypoint.sh tcga --help`
- **Dockerfile Path:** `./etl/Dockerfile.render`

**Variables de entorno:**
- `DATABASE_URL` → *(conexión automática desde PostgreSQL)*
- `GDC_PROJECTS` → `TCGA-BRCA`
- `DUCKDB_MEMORY_LIMIT` → `512MB`
- `GDC_DOWNLOAD_WORKERS` → `2`
- `LOG_LEVEL` → `INFO`

## Ejecutar el pipeline después del despliegue

Una vez que los servicios estén activos:

1. Ve al dashboard del Worker `tcga-etl`
2. Haz clic en **"Manual Deploy"** → **"Deploy latest commit"**
3. Una vez que el worker esté ejecutándose, abre su **"Shell"** (pestaña Shell)
4. Ejecuta los comandos en orden:

```bash
# 1. Inicializar esquema
tcga init-schema

# 2. Ejecutar pipeline completo
tcga pipeline --all

# 3. (Opcional) Ejecutar gold-extras para marcadores clínicos
tcga gold-extras --project TCGA-BRCA
```

**Nota:** En el plan gratis, la ejecución completa del ETL puede tardar **20-30 minutos** y descargar ~1.5 GB de datos. El worker se detendrá después de terminar.

## Problemas comunes

### Error: "Out of memory"
- Reduce `DUCKDB_MEMORY_LIMIT` a `256MB`
- Reduce `GDC_DOWNLOAD_WORKERS` a `1`
- Reduce `GDC_DOWNLOAD_BATCH_BYTES` a `200000000` (200 MB)

### Error: "No space left on device"
- El disco del worker gratis es limitado (~512 MB para /tmp)
- Asegúrate de que `DATA_DIR=/data` sea persistente (en Render no lo es)
- Considera ejecutar el ETL en batches separados:

```bash
# Ejecutar en pasos separados
tcga extract-cases --project TCGA-BRCA
tcga bronze-cases
tcga bronze-files
tcga download-expressions --batch-size 100000000  # 100 MB por batch
tcga bronze-expressions
tcga silver-case-expressions
tcga silver-clinical
tcga gold-extras --project TCGA-BRCA
```

### La web no se conecta a PostgreSQL
- Verifica que las variables `POSTGRES_*` estén correctas
- Asegúrate de que el PostgreSQL esté "Healthy"
- En el web service, verifica los logs: `Render Dashboard → tcga-web → Logs`

## Acceder a la aplicación
Una vez desplegado:
- La URL web será: `https://tcga-web.onrender.com`
- La página de estado: `https://tcga-web.onrender.com/status`
- Los casos: `https://tcga-web.onrender.com/casos`
- Los genes: `https://tcga-web.onrender.com/genes`

## Actualizar el despliegue
- Hacer cambios en el repositorio GitHub
- Render detectará automáticamente los cambios y desplegará el web service
- Para el ETL, necesitarás ejecutar manualmente los comandos nuevamente

## Notas de rendimiento
- Plan gratis: **512 MB RAM** por servicio
- PostgreSQL gratis: **1 GB RAM, 1 GB disco** (suficiente para TCGA-BRCA)
- El web service puede dormir después de 15 minutos de inactividad
- El primer acceso después de dormir puede tardar 30 segundos en "despertar"
- Considera el plan **Starter** ($7/mes) para mayor memoria y sin "sleep"
