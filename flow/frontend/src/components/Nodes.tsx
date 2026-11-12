import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { CatalogNode, NodeRuntime, WorkflowNode } from "../types";
import { STATE_LABELS } from "../api";
import { summarizeParams } from "../graph";

export type EtlNodeData = {
  definition: WorkflowNode;
  runtime: NodeRuntime | null;
  catalog: CatalogNode | null;
  color: string;
};

export type EtlNodeType = Node<EtlNodeData, "etl">;

const STATE_COLOR: Record<string, string> = {
  idle: "#dc2626",
  configured: "#eab308",
  running: "#2563eb",
  executed: "#15803d",
  error: "#b91c1c",
};

export function EtlNode({ data, selected }: NodeProps<EtlNodeType>) {
  const { definition, runtime, catalog, color } = data;
  const state = runtime?.state ?? "idle";
  const stateColor = STATE_COLOR[state] ?? "#64748b";
  const rows = runtime?.rows_out ?? 0;

  return (
    <div
      className={`etl-node${selected ? " selected" : ""}${catalog?.long_running ? " long" : ""}`}
      style={{ borderLeftColor: color }}
      title={definition.description}
    >
      <Handle type="target" position={Position.Left} className="port port-in" />
      <div className="etl-node-head">
        <span className="layer-chip" style={{ background: color }}>
          {definition.layer}
        </span>
        {catalog?.long_running && <span className="slow-chip">largo</span>}
        {definition.status !== "confirmed" && (
          <span className={`confidence ${definition.status}`}>{definition.status}</span>
        )}
      </div>
      <div className="etl-node-label">{definition.label}</div>
      <div className="etl-node-params">{summarizeParams(definition.params)}</div>
      <div className="semaforo" aria-label={`Estado: ${STATE_LABELS[state]}`}>
        <span className="semaforo-light" style={{ background: stateColor }} />
        <span className="semaforo-text">{STATE_LABELS[state]}</span>
        {rows > 0 && <span className="semaforo-rows">{rows.toLocaleString("es")} filas</span>}
      </div>
      <Handle type="source" position={Position.Right} className="port port-out" />
    </div>
  );
}

export type LaneNodeData = { layer: string; color: string };
export type LaneNodeType = Node<LaneNodeData, "lane">;

export function LaneNode({ data }: NodeProps<LaneNodeType>) {
  return (
    <div className="lane" style={{ borderColor: `${data.color}55` }}>
      <div className="lane-title" style={{ background: `${data.color}18`, color: data.color }}>
        {data.layer.toUpperCase()}
      </div>
    </div>
  );
}

export const nodeTypes = { etl: EtlNode, lane: LaneNode };
