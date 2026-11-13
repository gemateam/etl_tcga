# Diagrama de arquitectura — TCGA Medallion

Diagrama interactivo (HTML autónomo) de la arquitectura multi-cohorte: fuentes
externas, capas Medallón (Landing → Bronze → Silver → Gold), ejecutor visual
`flow` (estilo KNIME) y consumidores (`web`, API y exportaciones).

Abrir directamente en el navegador (no necesita servidor ni dependencias):

- `tcga-medallion-architecture.html` — diagrama con temas claro/oscuro y
  exportación a PNG/SVG.

## Contenido del directorio

| Archivo | Descripción |
|---|---|
| `tcga-medallion-architecture.html` | Artefacto final publicado (autónomo). |
| `candidate.json` | Fuente de verdad de nodos, aristas y geometría. |
| `finalize-summary.json` | Recibo de validación y diagnóstico de rutas. |
| `preview-light.png` / `preview-dark.png` | Previsualización estática 2048×1320. |

## Procedencia y validación

- Generado con `archify` (`quality: showcase`).
- Compuertas: `validate`, `deliver`, `check` y `browser-check` en **pass**.
- Resultado de ruteo: **0 cruces** y 2 desvíos solo informativos
  (`gdc → etl`, 3 quiebres/stretch 1,31; `engine → canvas`, 3 quiebres/stretch
  1,381), provocados por el ancho de las etiquetas en corredores de 30–50 px.
- Integridad verificada por SHA-256:
  - artefacto: `a4adb74855478ea0d0268c51c5a471117d6bf1e4ef7243fde6ca67268b1ea44e`
  - especificación (`candidate.json`): `4ec32225dd0e98c8e8569ba64a3cda9d2711217a581d14438545294850b229af`

## Regeneración

Desde la raíz del repositorio, con el skill de archify disponible:

```bash
node .agents/skills/archify/bin/archify.mjs finalize architecture \
  docs/architecture/candidate.json \
  docs/architecture/tcga-medallion-architecture.html \
  --quality showcase --out-dir <dir-de-salida> --json
```

Editar `candidate.json` (posiciones, etiquetas o aristas) y volver a ejecutar el
comando; el recibo `finalize-summary.json` debe quedar en `pass` antes de
reemplazar el HTML versionado.
