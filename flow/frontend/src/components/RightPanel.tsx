import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CatalogNode,
  JsonSchemaProperty,
  Lineage,
  LogLine,
  NodeRuntime,
  PreviewPage,
  Workflow,
  WorkflowNode,
} from "../types";
import { api, STATE_LABELS } from "../api";
import { LAYER_COLORS, formatBytes } from "../graph";

type Tab = "description" | "config" | "output" | "log" | "lineage" | "notes";

const TABS: { key: Tab; label: string }[] = [
  { key: "description", label: "Descripción" },
  { key: "config", label: "Configuración" },
  { key: "output", label: "Salida" },
  { key: "log", label: "Log" },
  { key: "lineage", label: "Linaje" },
  { key: "notes", label: "Notas" },
];

interface Props {
  workflow: Workflow;
  node: WorkflowNode | null;
  catalog: CatalogNode | null;
  runtime: NodeRuntime | null;
  liveLogs: LogLine[];
  onWorkflowChange: (workflow: Workflow) => void;
  onRuntimeRefresh: () => void;
  onDeleteNode: (nodeId: string) => void;
  onNotice: (message: string, error?: boolean) => void;
}

interface FieldInfo {
  kind: "string" | "integer" | "number" | "boolean" | "enum";
  nullable: boolean;
  options: string[];
  description?: string;
}

function fieldInfo(property?: JsonSchemaProperty): FieldInfo {
  if (!property) return { kind: "string", nullable: false, options: [] };
  if (property.enum) {
    return {
      kind: "enum",
      nullable: false,
      options: property.enum,
      description: property.description,
    };
  }
  const candidates = property.anyOf ?? [property];
  const nonNull = candidates.find((item) => item.type && item.type !== "null") ?? property;
  const nullable = candidates.some((item) => item.type === "null");
  const type = Array.isArray(nonNull.type) ? nonNull.type.find((t) => t !== "null") : nonNull.type;
  if (type === "boolean") return { kind: "boolean", nullable, options: [] };
  if (type === "integer") return { kind: "integer", nullable, options: [] };
  if (type === "number") return { kind: "number", nullable, options: [] };
  return {
    kind: "string",
    nullable,
    options: [],
    description: property.description,
  };
}

export function RightPanel({
  workflow,
  node,
  catalog,
  runtime,
  liveLogs,
  onWorkflowChange,
  onRuntimeRefresh,
  onDeleteNode,
  onNotice,
}: Props) {
  const [tab, setTab] = useState<Tab>("description");
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [confidence, setConfidence] = useState<WorkflowNode["status"]>("confirmed");
  const [saving, setSaving] = useState(false);

  const [outputKey, setOutputKey] = useState<string | undefined>(undefined);
  const [preview, setPreview] = useState<PreviewPage | null>(null);
  const [page, setPage] = useState(1);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [logs, setLogs] = useState<LogLine[]>([]);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [lineageError, setLineageError] = useState<string | null>(null);

  useEffect(() => {
    setTab("description");
    setForm({ ...(node?.params ?? {}) });
    setLabel(node?.label ?? "");
    setNotes(node?.notes ?? "");
    setConfidence(node?.status ?? "confirmed");
    setOutputKey(catalog?.output_specs[0]?.key);
    setPreview(null);
    setPreviewError(null);
    setPage(1);
    setLogs([]);
    setLineage(null);
    setLineageError(null);
  }, [node?.id, catalog?.key]);

  const outputOptions = catalog?.output_specs ?? [];

  const loadPreview = useCallback(
    async (targetPage: number) => {
      if (!node) return;
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const payload = await api.output(workflow.id, node.id, outputKey, targetPage, 100);
        setPreview(payload);
      } catch (error) {
        setPreview(null);
        setPreviewError(error instanceof Error ? error.message : String(error));
      } finally {
        setPreviewLoading(false);
      }
    },
    [workflow.id, node?.id, outputKey],
  );

  useEffect(() => {
    if (tab === "output" && node) {
      void loadPreview(page);
    }
  }, [tab, page, node?.id, outputKey, loadPreview]);

  useEffect(() => {
    if (tab !== "log" || !node) return;
    let alive = true;
    const fetchLog = async () => {
      try {
        const payload = await api.log(workflow.id, node.id);
        if (alive) setLogs(payload.lines);
      } catch {
        /* el log local por SSE sigue disponible */
      }
    };
    void fetchLog();
    const interval = window.setInterval(fetchLog, 2500);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, [tab, workflow.id, node?.id]);

  useEffect(() => {
    if (tab !== "lineage" || !node) return;
    let alive = true;
    setLineageError(null);
    api
      .lineage(workflow.id, node.id)
      .then((payload) => {
        if (alive) setLineage(payload);
      })
      .catch((error) => {
        if (alive) setLineageError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [tab, workflow.id, node?.id]);

  const mergedLogs = useMemo(() => {
    const bySeq = new Map<number, LogLine>();
    for (const line of logs) bySeq.set(line.seq, line);
    for (const line of liveLogs) {
      if (line.node_id === node?.id) bySeq.set(line.seq, line);
    }
    return [...bySeq.values()].sort((a, b) => a.seq - b.seq).slice(-500);
  }, [logs, liveLogs, node?.id]);

  if (!node) {
    return (
      <aside className="right-panel empty">
        <p>Selecciona un nodo del lienzo para ver su configuración, salida y linaje.</p>
      </aside>
    );
  }

  const saveParams = async () => {
    setSaving(true);
    try {
      const updated = await api.patchNode(workflow.id, node.id, { params: form });
      onWorkflowChange(updated);
      onNotice("Parámetros guardados");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error), true);
    } finally {
      setSaving(false);
    }
  };

  const saveMeta = async () => {
    setSaving(true);
    try {
      const updated = await api.patchNode(workflow.id, node.id, {
        label,
        notes,
        status: confidence,
      });
      onWorkflowChange(updated);
      onNotice("Nodo actualizado");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error), true);
    } finally {
      setSaving(false);
    }
  };

  const renderField = (key: string, property?: JsonSchemaProperty) => {
    const info = fieldInfo(property);
    const value = form[key];
    const common = {
      id: `param-${key}`,
      "aria-label": key,
    };
    if (info.kind === "boolean") {
      return (
        <label className="check" key={key}>
          <input
            {...common}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(event) => setForm({ ...form, [key]: event.target.checked })}
          />
          <span>{key}</span>
        </label>
      );
    }
    if (info.kind === "enum") {
      return (
        <label key={key}>
          <span>{key}</span>
          <select
            {...common}
            value={String(value ?? "")}
            onChange={(event) => setForm({ ...form, [key]: event.target.value })}
          >
            {info.nullable && <option value="">—</option>}
            {info.options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      );
    }
    return (
      <label key={key}>
        <span>
          {key}
          {info.nullable && <em> (opcional)</em>}
        </span>
        <input
          {...common}
          type={info.kind === "integer" || info.kind === "number" ? "number" : "text"}
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(event) => {
            const raw = event.target.value;
            if (info.kind === "integer" || info.kind === "number") {
              setForm({ ...form, [key]: raw === "" ? null : Number(raw) });
            } else {
              setForm({ ...form, [key]: raw === "" && info.nullable ? null : raw });
            }
          }}
        />
        {info.description && <small className="hint">{info.description}</small>}
      </label>
    );
  };

  const properties = catalog?.params_schema?.properties ?? {};

  return (
    <aside className="right-panel">
      <header className="right-head">
        <div>
          <span className="layer-chip" style={{ background: LAYER_COLORS[node.layer] }}>
            {node.layer}
          </span>
          <h3>{node.label}</h3>
          <code>{node.type}</code>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
          <span className={`state-pill ${runtime?.state ?? "idle"}`}>
            {STATE_LABELS[runtime?.state ?? "idle"]}
          </span>
          <button
            className="danger"
            style={{ padding: "4px 8px", fontSize: "11px" }}
            title="Eliminar este nodo del pipeline"
            onClick={() => {
              if (window.confirm(`¿Eliminar el nodo ${node.label}?`)) {
                onDeleteNode(node.id);
              }
            }}
          >
            🗑️ Eliminar nodo
          </button>
        </div>
      </header>

      <nav className="panel-tabs right-tabs">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={tab === item.key ? "active" : ""}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="right-body">
        {tab === "description" && (
          <div className="stack">
            <p>{node.description || catalog?.description || "Sin descripción."}</p>
            {catalog && (
              <div className="ports">
                <div>
                  <h4>Entradas</h4>
                  {catalog.inputs.length === 0 && <span className="muted">Ninguna</span>}
                  {catalog.inputs.map((port) => (
                    <span key={port.name} className="port-chip">
                      {port.name}: {port.kind}
                    </span>
                  ))}
                </div>
                <div>
                  <h4>Salidas</h4>
                  {catalog.outputs.map((port) => (
                    <span key={port.name} className="port-chip">
                      {port.name}: {port.kind}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <dl className="facts">
              <dt>Ejecución</dt>
              <dd>{runtime?.detail ?? "—"}</dd>
              <dt>Filas</dt>
              <dd>{runtime?.rows_out?.toLocaleString("es") ?? "—"}</dd>
              <dt>Duración</dt>
              <dd>{runtime?.duration_seconds ? `${runtime.duration_seconds} s` : "—"}</dd>
              <dt>run_id</dt>
              <dd>{runtime?.run_id ?? "—"}</dd>
            </dl>
          </div>
        )}

        {tab === "config" && (
          <div className="stack">
            {Object.keys(properties).length === 0 && (
              <p className="muted">Este nodo no tiene parámetros.</p>
            )}
            <div className="form-grid">
              {Object.entries(properties).map(([key, property]) => renderField(key, property))}
            </div>
            {Object.keys(properties).length > 0 && (
              <button className="primary" onClick={saveParams} disabled={saving}>
                {saving ? "Guardando…" : "Guardar parámetros"}
              </button>
            )}
          </div>
        )}

        {tab === "output" && (
          <div className="stack">
            {outputOptions.length > 1 && (
              <label>
                <span>Salida</span>
                <select
                  value={outputKey ?? ""}
                  onChange={(event) => {
                    setOutputKey(event.target.value || undefined);
                    setPage(1);
                  }}
                >
                  {outputOptions.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label} · {option.kind}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {previewError && <p className="error-text">{previewError}</p>}
            {previewLoading && <p className="muted">Cargando vista previa…</p>}
            {preview && (
              <>
                <div className="preview-meta">
                  <span>
                    {preview.total_rows?.toLocaleString("es") ?? "?"} filas · {formatBytes(preview.size_bytes)}
                  </span>
                  <span>
                    página {preview.page} · {preview.page_size} por página
                  </span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        {preview.columns.map((column) => (
                          <th key={column.name} title={column.type}>
                            {column.name}
                            <small>{column.type}</small>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row, index) => (
                        <tr key={index}>
                          {preview.columns.map((column) => (
                            <td key={column.name}>{formatCell(row[column.name])}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="pager">
                  <button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1}>
                    Anterior
                  </button>
                  <button
                    onClick={() => setPage((current) => current + 1)}
                    disabled={preview.total_rows !== null && page * preview.page_size >= preview.total_rows}
                  >
                    Siguiente
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {tab === "log" && (
          <div className="stack">
            <button className="ghost" onClick={() => setLogs([])}>
              Limpiar vista
            </button>
            <div className="log-view">
              {mergedLogs.length === 0 && <p className="muted">Sin líneas de log todavía.</p>}
              {mergedLogs.map((line) => (
                <div key={`${line.seq}-${line.ts}`} className={`log-line ${line.level.toLowerCase()}`}>
                  <span className="log-ts">{line.ts.slice(11, 19)}</span>
                  <span className="log-level">{line.level}</span>
                  <span className="log-msg">{line.message}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "lineage" && (
          <div className="stack">
            {lineageError && <p className="error-text">{lineageError}</p>}
            {lineage?.nodes.map((item, index) => (
              <div key={item.id} className={`lineage-item${item.is_target ? " target" : ""}`}>
                <div className="lineage-head">
                  <span className="layer-chip" style={{ background: LAYER_COLORS[item.layer] }}>
                    {item.layer}
                  </span>
                  <strong>{item.label}</strong>
                  {index === 0 && <span className="muted">origen</span>}
                  {item.is_target && <span className="muted">destino</span>}
                </div>
                <ul>
                  {item.outputs.map((output) => (
                    <li key={output.key}>
                      <code>{output.target}</code> <small>{output.kind}</small>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}

        {tab === "notes" && (
          <div className="stack">
            <label>
              <span>Nombre del nodo</span>
              <input value={label} onChange={(event) => setLabel(event.target.value)} />
            </label>
            <label>
              <span>Confianza de la definición</span>
              <select
                value={confidence}
                onChange={(event) => setConfidence(event.target.value as WorkflowNode["status"])}
              >
                <option value="confirmed">confirmed</option>
                <option value="inferred">inferred</option>
                <option value="unknown">unknown</option>
              </select>
            </label>
            <label>
              <span>Notas</span>
              <textarea
                rows={6}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Decisiones, huecos o correcciones de este nodo…"
              />
            </label>
            <div className="row">
              <button className="primary" onClick={saveMeta} disabled={saving}>
                {saving ? "Guardando…" : "Guardar"}
              </button>
              <button
                className="danger"
                onClick={() => {
                  if (window.confirm(`¿Eliminar el nodo ${node.label}?`)) {
                    onDeleteNode(node.id);
                  }
                }}
              >
                Eliminar nodo
              </button>
            </div>
            <button className="ghost" onClick={onRuntimeRefresh}>
              Refrescar estado
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 300);
  return String(value);
}
