import type { Edge, Node } from "@xyflow/react";
import type {
  CatalogNode,
  Layer,
  Workflow,
  WorkflowRuntime,
} from "./types";

export const LANE_ORDER: Layer[] = ["landing", "bronze", "silver", "gold"];

export const LAYER_COLORS: Record<Layer, string> = {
  landing: "#0f766e",
  bronze: "#d97706",
  silver: "#2563eb",
  gold: "#7c3aed",
};

const DEFAULT_LANE_WIDTH = 320;
const NODE_WIDTH_ESTIMATE = 250;
const LANE_PADDING = 30;

export function dynamicLaneNodes(workflow?: Workflow): Node[] {
  // Calcular límites por capa
  let currentX = 0;
  const nodes = workflow?.nodes ?? [];
  
  // Altura máxima basada en los nodos existentes
  const maxY = nodes.reduce((acc, n) => Math.max(acc, n.position.y + 160), 980);
  const laneHeight = Math.max(maxY + 40, 980);

  return LANE_ORDER.map((layer) => {
    const layerNodes = nodes.filter((n) => n.layer === layer);
    let width = DEFAULT_LANE_WIDTH;
    let startX = currentX;

    if (layerNodes.length > 0) {
      const minNodeX = Math.min(...layerNodes.map((n) => n.position.x));
      const maxNodeX = Math.max(...layerNodes.map((n) => n.position.x));
      // Abarca desde la posición del nodo más a la izquierda hasta el más a la derecha + ancho del nodo
      const span = maxNodeX - minNodeX + NODE_WIDTH_ESTIMATE + LANE_PADDING * 2;
      width = Math.max(DEFAULT_LANE_WIDTH, span);
      // Alineamos la swimlane para que empiece justo antes del primer nodo si es necesario
      startX = Math.min(currentX, minNodeX - LANE_PADDING);
    }

    const laneNode: Node = {
      id: `lane_${layer}`,
      type: "lane",
      position: { x: startX, y: 0 },
      data: { layer, color: LAYER_COLORS[layer] },
      draggable: false,
      selectable: false,
      connectable: false,
      zIndex: -1,
      style: { width, height: laneHeight },
    };

    currentX = startX + width;
    return laneNode;
  });
}

export function laneNodes(): Node[] {
  return dynamicLaneNodes();
}

export function buildNodes(
  workflow: Workflow,
  runtime: WorkflowRuntime | null,
  catalog: CatalogNode[],
): Node[] {
  const catalogByKey = new Map(catalog.map((item) => [item.key, item]));
  const states = new Map(
    (runtime?.nodes ?? []).map((item) => [item.id, item]),
  );
  return [
    ...dynamicLaneNodes(workflow),
    ...workflow.nodes.map((node) => ({
      id: node.id,
      type: "etl",
      position: { x: node.position.x, y: node.position.y },
      data: {
        definition: node,
        runtime: states.get(node.id) ?? null,
        catalog: catalogByKey.get(node.type) ?? null,
        color: LAYER_COLORS[node.layer],
      },
      zIndex: 1,
    })),
  ];
}

export function buildEdges(
  workflow: Workflow,
  runtime: WorkflowRuntime | null,
): Edge[] {
  const running = new Set(
    (runtime?.nodes ?? [])
      .filter((item) => item.state === "running")
      .map((item) => item.id),
  );
  return workflow.edges.map((edge) => ({
    id: `${edge.source}->${edge.target}`,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    animated: running.has(edge.source) || running.has(edge.target),
    style: { stroke: "#94a3b8", strokeWidth: 1.6 },
  }));
}

export function summarizeParams(params: Record<string, unknown>): string {
  const entries = Object.entries(params).filter(([, value]) => value !== null && value !== "");
  if (entries.length === 0) return "Sin parámetros";
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}

export function formatBytes(value: number | null): string {
  if (value === null || value === undefined) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
