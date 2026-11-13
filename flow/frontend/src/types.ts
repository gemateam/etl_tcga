export type Layer = "landing" | "bronze" | "silver" | "gold";
export type NodeState = "idle" | "configured" | "running" | "executed" | "error";
export type NodeConfidence = "confirmed" | "inferred" | "unknown";

export interface Port {
  name: string;
  kind: string;
}

export interface OutputSpec {
  key: string;
  label: string;
  kind: "table" | "parquet" | "json";
  target: string;
}

export interface CatalogNode {
  key: string;
  label: string;
  layer: Layer;
  description: string;
  inputs: Port[];
  outputs: Port[];
  output_specs: OutputSpec[];
  long_running: boolean;
  params_schema: {
    properties?: Record<string, JsonSchemaProperty>;
    required?: string[];
  };
}

export interface JsonSchemaProperty {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  anyOf?: JsonSchemaProperty[];
}

export interface WorkflowNode {
  id: string;
  type: string;
  label: string;
  layer: Layer;
  position: { x: number; y: number };
  params: Record<string, unknown>;
  notes: string;
  status: NodeConfidence;
  description: string;
}

export interface WorkflowEdge {
  source: string;
  target: string;
}

export interface Workflow {
  id: string;
  name: string;
  version: number;
  description: string;
  project_id: string;
  variables: Record<string, unknown>;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface WorkflowSummary {
  id: string;
  name: string;
  version: number;
  description: string;
  project_id: string;
  node_count: number;
  edge_count: number;
}

export interface NodeRuntime {
  id: string;
  state: NodeState;
  detail: string | null;
  run_id: string | null;
  rows_in: number;
  rows_out: number;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  error: string | null;
}

export interface WorkflowRuntime {
  workflow_id: string;
  execution: "idle" | "running" | "cancelling";
  current_node: string | null;
  queue: string[];
  nodes: NodeRuntime[];
}

export interface PreviewColumn {
  name: string;
  type: string;
}

export interface PreviewPage {
  output: string;
  kind: "table" | "parquet" | "json";
  columns: PreviewColumn[];
  rows: Record<string, unknown>[];
  total_rows: number | null;
  page: number;
  page_size: number;
  size_bytes: number | null;
}

export interface LogLine {
  seq: number;
  ts: string;
  node_id: string | null;
  level: string;
  message: string;
}

export interface AppConfig {
  layers: { key: Layer; color: string; label: string }[];
  states: Record<NodeState, string>;
  workflows: WorkflowSummary[];
  data_dir: string;
  gemamx_dir: string;
}

export interface LineageNode {
  id: string;
  label: string;
  type: string;
  layer: Layer;
  status: NodeConfidence;
  is_target: boolean;
  parents: string[];
  outputs: OutputSpec[];
}

export interface Lineage {
  workflow_id: string;
  target: string;
  nodes: LineageNode[];
  edges: WorkflowEdge[];
}

export interface NodeSource {
  module: string;
  function: string;
  code: string;
  language: string;
}
