# Backlog de producto: TCGA Medallion

## Propósito y alcance

Este documento organiza en épicas, features e historias de usuario las capacidades presentes en el proyecto. Es un inventario del producto implementado, no una lista de trabajo futuro. Los criterios de aceptación resumen el comportamiento disponible en la CLI, los ETL, la aplicación web y los reportes.

**Estado general:** historias implementadas.  
**Producto:** plataforma de ingesta, transformación, consulta y reporte de datos abiertos de TCGA/GDC.  
**Proyecto predeterminado:** `TCGA-BRCA`; admite proyectos configurados mediante `GDC_PROJECTS`.

## Épica E1. Ingesta de datos abiertos del GDC

**Objetivo:** obtener y conservar datos clínicos, manifiestos y archivos de expresión y mutaciones desde la API pública del GDC.

### Feature F1.1. Configuración de proyectos y cliente GDC

#### HU-1.1.1. Configurar proyectos de análisis

Como operador del pipeline, quiero configurar uno o más proyectos TCGA para ejecutar la ingesta sin modificar el código.

- **Criterios de aceptación:** `TCGA-BRCA` es el proyecto predeterminado; se pueden indicar proyectos adicionales por configuración o CLI.
- **Estado:** Implementada.

#### HU-1.1.2. Consultar el GDC de forma resiliente y abierta

Como operador, quiero consultar la API pública con paginación y reintentos, y limitar las descargas a datos abiertos.

- **Criterios de aceptación:** el cliente centraliza consultas, paginación, límites de tiempo y reintentos; los archivos controlados pueden inventariarse, pero no se descargan.
- **Estado:** Implementada.

### Feature F1.2. Extracción a landing

#### HU-1.2.1. Extraer casos y manifiestos de expresión

Como ingeniero de datos, quiero guardar en landing las páginas de casos y el manifiesto de expresión del proyecto para conservar la entrada original del pipeline.

- **Criterios de aceptación:** la CLI ofrece `extract-cases` y `extract-files-manifest`; los resultados se particionan por proyecto y registran los metadatos de origen disponibles.
- **Estado:** Implementada.

#### HU-1.2.2. Inventariar y preparar archivos de mutaciones

Como analista, quiero incorporar el manifiesto MAF y sus mutaciones al flujo medallion para consultar variantes somáticas junto con los datos clínicos.

- **Criterios de aceptación:** la CLI ofrece `extract-maf-manifest`, `bronze-mutations` y `silver-mutations`; la capa silver conserva variantes PASS normalizadas y deduplicadas.
- **Estado:** Implementada.

### Feature F1.3. Descarga de expresión génica

#### HU-1.3.1. Descargar expresión de forma reanudable

Como operador, quiero descargar archivos de expresión en lotes y retomar una ejecución interrumpida sin repetir descargas completas.

- **Criterios de aceptación:** la CLI `download-expressions` admite límite de archivos y reintento de fallidos; el control de descargas registra estados y valida los archivos mediante MD5.
- **Estado:** Implementada.

## Épica E2. Transformación medallion y productos analíticos

**Objetivo:** transformar las respuestas del GDC en datos trazables, normalizados y listos para análisis.

### Feature F2.1. Capas landing y bronze

#### HU-2.1.1. Conservar datos originales y crear tablas bronze

Como ingeniero de datos, quiero conservar las respuestas originales de casos y convertir los datos de casos y archivos a formatos consultables.

- **Criterios de aceptación:** los casos originales se conservan en `bronze.cases_raw`; casos y manifiestos se aplanan a Parquet; las expresiones se convierten a Parquet con DuckDB y se excluyen las filas de control del GDC.
- **Estado:** Implementada.

### Feature F2.2. Capa silver clínica, molecular y de expresión

#### HU-2.2.1. Consultar clínica normalizada por caso

Como analista, quiero consultar casos, demografía, diagnósticos, exposiciones y muestras con llaves y tipos consistentes.

- **Criterios de aceptación:** `silver-clinical` carga entidades clínicas separadas en PostgreSQL y mantiene sus relaciones con el caso.
- **Estado:** Implementada.

#### HU-2.2.2. Relacionar expresión con caso y muestra

Como analista, quiero consultar la expresión génica asociada a archivo, caso y muestra para comparar perfiles.

- **Criterios de aceptación:** `silver-case-expressions` genera datos Parquet en formato narrow con identificadores de caso, muestra y archivo, además de las métricas de expresión.
- **Estado:** Implementada.

#### HU-2.2.3. Consultar variantes somáticas normalizadas

Como investigador, quiero consultar mutaciones somáticas filtradas y normalizadas por caso y gen.

- **Criterios de aceptación:** `silver-mutations` publica variantes PASS en `silver.mutations` y permite relacionarlas con los casos.
- **Estado:** Implementada.

### Feature F2.3. Agregados gold para consulta y visualización

#### HU-2.3.1. Explorar diagnósticos, muestras y estadísticas génicas

Como analista, quiero usar agregados precalculados para revisar la composición de la cohorte y las estadísticas de expresión.

- **Criterios de aceptación:** `gold-extras` genera resúmenes de muestras, estadísticas por gen y tipo de muestra, dimensión de genes y valores de marcadores.
- **Estado:** Implementada.

#### HU-2.3.2. Generar el top de diagnósticos y el pivote de expresión

Como analista, quiero obtener los diagnósticos más frecuentes y una matriz de expresión por muestra con los genes más variables.

- **Criterios de aceptación:** `gold-top-diagnosis` genera el top 20; `gold-cx-pivot` permite elegir número de genes, métrica, transformación log2 y tipo de muestra, y publica el resultado para consumo analítico.
- **Estado:** Implementada.

## Épica E3. Orquestación y operación del pipeline

**Objetivo:** ejecutar etapas completas o individuales, registrar su resultado y observar el estado operativo.

### Feature F3.1. CLI y ejecución por capas

#### HU-3.1.1. Ejecutar el pipeline completo o por etapas

Como operador, quiero ejecutar el pipeline completo o seleccionar las capas necesarias para controlar la ingesta y el reproceso.

- **Criterios de aceptación:** `tcga run` encadena landing, bronze, silver y gold en orden; acepta selección de capas y parámetros analíticos; se detiene ante un fallo.
- **Estado:** Implementada.

#### HU-3.1.2. Ejecutar ETL individuales y consultar estado

Como operador, quiero invocar pasos individuales y consultar el estado de las ejecuciones y los conteos de las capas.

- **Criterios de aceptación:** la CLI expone comandos por etapa, `status`, inicialización de base de datos y comprobaciones operativas del servicio web y la versión GDC.
- **Estado:** Implementada.

### Feature F3.2. Seguimiento de ejecuciones, descargas y calidad

#### HU-3.2.1. Revisar historial y progreso del pipeline

Como operador, quiero ver las últimas ejecuciones, la versión GDC, el progreso de descargas y las alertas de calidad.

- **Criterios de aceptación:** la vista de estado presenta ejecuciones, conteos, progreso de descargas, release GDC y verificaciones de calidad disponibles.
- **Estado:** Implementada.

#### HU-3.2.2. Detectar cambios de release

Como operador, quiero comprobar la versión del GDC y automatizar la revisión periódica si activo el servicio opcional.

- **Criterios de aceptación:** hay comandos para revisar el release y un servicio scheduler opcional configurable mediante Docker Compose.
- **Estado:** Implementada.

## Épica E4. Exploración de cohortes en la aplicación web

**Objetivo:** consultar de forma segura los datos silver y gold desde una interfaz en español.

### Feature F4.1. Estado y exploración de casos

#### HU-4.1.1. Filtrar, paginar y exportar casos

Como investigador, quiero localizar casos mediante filtros clínicos y descargar el resultado para análisis posterior.

- **Criterios de aceptación:** `/casos` ofrece filtros de cohorte, paginación en servidor y exportación CSV respetando los filtros aplicados.
- **Estado:** Implementada.

#### HU-4.1.2. Consultar una ficha clínica por caso

Como investigador, quiero revisar demografía, diagnósticos, exposiciones, muestras y archivos asociados a un caso.

- **Criterios de aceptación:** la ficha `/casos/{case_id}` muestra entidades relacionadas, gestiona casos inexistentes con una respuesta clara y resuelve alias verificados cuando están registrados.
- **Estado:** Implementada.

### Feature F4.2. Mutaciones somáticas

#### HU-4.2.1. Explorar mutaciones de la cohorte

Como investigador, quiero revisar genes mutados con mayor frecuencia, tipos de variante y distribución cromosómica en la población seleccionada.

- **Criterios de aceptación:** `/mutaciones` presenta resúmenes por etnicidad y proyecto, con genes destacados y distribuciones de variantes.
- **Estado:** Implementada.

#### HU-4.2.2. Consultar mutaciones de un caso

Como investigador, quiero inspeccionar las variantes de un caso sin cargar toda la tabla de una vez.

- **Criterios de aceptación:** la ficha de caso incluye el acceso a mutaciones y la tabla se pagina en el servidor.
- **Estado:** Implementada.

### Feature F4.3. Expresión por gen y reportes de cohorte

#### HU-4.3.1. Analizar expresión de un gen

Como analista, quiero buscar un gen y comparar su expresión por tipo de muestra y estadio.

- **Criterios de aceptación:** `/genes` permite búsqueda/autocompletado, selección de métrica y escala log2, consulta estadísticas y percentiles, y exporta estadísticas CSV.
- **Estado:** Implementada.

#### HU-4.3.2. Consultar reportes de cohorte

Como investigador, quiero visualizar diagnósticos, demografía, muestras y variabilidad de expresión para el proyecto seleccionado.

- **Criterios de aceptación:** `/reportes` presenta los reportes R1-R4; incluye visualizaciones de top de diagnósticos, demografía, muestras, mapa de calor y PCA, exportables a CSV; muestra Kaplan-Meier por estadio cuando hay datos disponibles.
- **Estado:** Implementada.

## Épica E5. Reportes clínicos y moleculares por caso

**Objetivo:** generar una vista ampliada de un caso a partir de datos abiertos del GDC y suplementos locales opcionales.

### Feature F5.1. Generación y resolución de reportes

#### HU-5.1.1. Generar reporte por identificador de caso

Como investigador, quiero generar un reporte por `submitter_id` o UUID para reunir información clínica y molecular de un caso.

- **Criterios de aceptación:** `case-report` genera un JSON local con metadatos de generación y release; soporta refresco y alias legado verificado.
- **Estado:** Implementada.

#### HU-5.1.2. Generar reportes por lote

Como operador, quiero procesar reportes para una cohorte y continuar aunque falle un caso individual.

- **Criterios de aceptación:** `case-report-batch` permite limitar el lote, reanudar mediante resultados y cachés válidos, y omitir percentiles cuando se solicita.
- **Estado:** Implementada.

### Feature F5.2. Contenido integrado del reporte

#### HU-5.2.1. Revisar clínica, supervivencia y cronología

Como investigador, quiero consultar la clínica expandida, el seguimiento, la estadificación TNM y los eventos disponibles en una línea temporal.

- **Criterios de aceptación:** el reporte integra las capas clínicas disponibles y distingue la ausencia de datos de un resultado clínico negativo.
- **Estado:** Implementada.

#### HU-5.2.2. Revisar resultados moleculares y expresión

Como investigador, quiero consultar mutaciones driver, número de copias, receptores clínicos disponibles y expresión de marcadores en un mismo reporte.

- **Criterios de aceptación:** el reporte reúne las capas disponibles, indica las faltantes y no presenta la expresión de ARN como confirmación de positividad clínica.
- **Estado:** Implementada.

#### HU-5.2.3. Comparar expresión con cohortes

Como investigador, quiero comparar la expresión del caso con cohortes de referencia para interpretar sus percentiles.

- **Criterios de aceptación:** el cálculo opcional admite cohortes TCGA-BRCA y subgrupos disponibles; conserva caché por release/panel y permite limitar archivos y concurrencia.
- **Estado:** Implementada.

### Feature F5.3. Presentación del reporte en la web

#### HU-5.3.1. Consultar e imprimir el reporte desde la ficha

Como investigador, quiero revisar el JSON generado en la ficha de caso y obtener una vista imprimible con la información ampliada.

- **Criterios de aceptación:** la ficha y su vista PDF incorporan el reporte cacheado si existe; la vista imprimible reúne clínica, marcadores, variantes, expresión y cronología disponibles.
- **Estado:** Implementada.

## Épica E6. Despliegue, seguridad y calidad

**Objetivo:** ejecutar el producto de forma reproducible y proteger los datos y servicios.

### Feature F6.1. Entorno reproducible

#### HU-6.1.1. Levantar los servicios con Docker Compose

Como desarrollador, quiero iniciar la base de datos y la web con una configuración versionada para reproducir el entorno local.

- **Criterios de aceptación:** Docker Compose define PostgreSQL, ETL, web y servicios opcionales; los datos persistentes se montan en volúmenes y web publica el puerto configurable.
- **Estado:** Implementada.

#### HU-6.1.2. Validar disponibilidad y acceso de solo lectura

Como responsable de operación, quiero detectar servicios no disponibles y limitar la web a consultas de datos.

- **Criterios de aceptación:** PostgreSQL y web tienen healthchecks; la aplicación expone `/health`; las consultas de la web son de solo lectura y usan parámetros SQL.
- **Estado:** Implementada.

### Feature F6.2. Pruebas y reproducibilidad

#### HU-6.2.1. Verificar los contratos principales sin depender de la red

Como mantenedor, quiero ejecutar pruebas automatizadas con respuestas simuladas para validar el cliente GDC, las transformaciones y la web.

- **Criterios de aceptación:** el conjunto incluye pruebas unitarias, fixtures e integración de ETL/web; la ejecución habitual no requiere acceso a la API real y el smoke test está separado.
- **Estado:** Implementada.

#### HU-6.2.2. Repetir transformaciones con resultados controlados

Como operador, quiero relanzar pasos del pipeline sin duplicar datos ni perder trazabilidad.

- **Criterios de aceptación:** los pasos registran estado y resultados operativos, y las descargas completadas se controlan para poder reanudar ejecuciones.
- **Estado:** Implementada.

## Referencias de implementación

- `README.md`: arquitectura, comandos, capas de datos, páginas web y operación.
- `etl/src/tcga/cli.py`: comandos ETL, orquestación, reportes y comprobaciones.
- `web/app/main.py`: rutas web de estado, casos, mutaciones, expresión y reportes.
- `gdc_case_report.py`: generación del reporte clínico/molecular ampliado.
- `tests/`: pruebas de cliente GDC, expresiones, integración, web y reportes por caso.