import { useMemo, useState } from "react";
import type { CatalogNode, Layer, WorkflowSummary } from "../types";
import { LANE_ORDER, LAYER_COLORS } from "../graph";

interface Props {
  workflows: WorkflowSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  catalog: CatalogNode[];
  onToggleCollapse?: () => void;
}

export function LeftPanel({ workflows, activeId, onSelect, catalog, onToggleCollapse }: Props) {
  const [tab, setTab] = useState<"workflows" | "nodes">("workflows");
  const grouped = useMemo(() => {
    const result = new Map<Layer, CatalogNode[]>();
    for (const layer of LANE_ORDER) result.set(layer, []);
    for (const node of catalog) {
      result.get(node.layer)?.push(node);
    }
    return result;
  }, [catalog]);

  return (
    <aside className="left-panel">
      <div className="panel-tabs">
        <button
          className={tab === "workflows" ? "active" : ""}
          onClick={() => setTab("workflows")}
        >
          Flujos
        </button>
        <button className={tab === "nodes" ? "active" : ""} onClick={() => setTab("nodes")}>
          Nodos
        </button>
        {onToggleCollapse && (
          <button
            className="ghost"
            style={{ flex: "0 0 36px", padding: "8px", fontSize: "12px", borderBottom: "none" }}
            title="Colapsar panel izquierdo"
            onClick={onToggleCollapse}
          >
            ✕
          </button>
        )}
      </div>

      {tab === "workflows" && (
        <div className="workflow-list">
          {workflows.map((workflow) => (
            <button
              key={workflow.id}
              className={`workflow-item${workflow.id === activeId ? " active" : ""}`}
              onClick={() => onSelect(workflow.id)}
            >
              <strong>{workflow.name}</strong>
              <span>
                {workflow.node_count} nodos · {workflow.edge_count} aristas
              </span>
              <small>{workflow.project_id}</small>
            </button>
          ))}
          {workflows.length === 0 && <p className="muted">No hay definiciones de workflow.</p>}
        </div>
      )}

      {tab === "nodes" && (
        <div className="node-repo">
          <p className="hint">Arrastra un nodo al lienzo para añadirlo.</p>
          {LANE_ORDER.map((layer) => (
            <section key={layer} className="repo-group">
              <h4 style={{ color: LAYER_COLORS[layer] }}>{layer}</h4>
              {(grouped.get(layer) ?? []).map((node) => (
                <div
                  key={node.key}
                  className="repo-item"
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData("application/tcga-node", node.key);
                    event.dataTransfer.effectAllowed = "copy";
                  }}
                  title={node.description}
                >
                  <span className="dot" style={{ background: LAYER_COLORS[layer] }} />
                  <span>{node.label}</span>
                  {node.long_running && <small>largo</small>}
                </div>
              ))}
            </section>
          ))}
        </div>
      )}
    </aside>
  );
}
