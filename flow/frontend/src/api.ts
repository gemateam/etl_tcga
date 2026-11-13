import type {
  AppConfig,
  CatalogNode,
  Lineage,
  LogLine,
  NodeSource,
  NodeState,
  PreviewPage,
  Workflow,
  WorkflowRuntime,
} from "./types";

const BASE = "/api/flow";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  config: () => request<AppConfig>("/config"),
  catalog: () => request<{ nodes: CatalogNode[] }>("/catalog"),
  workflows: () => request<{ workflows: AppConfig["workflows"] }>("/workflows"),
  workflow: (id: string) => request<Workflow>(`/workflows/${id}`),
  state: (id: string) => request<WorkflowRuntime>(`/workflows/${id}/state`),
  execute: (id: string, body: { nodes?: string[]; until?: string; all?: boolean }) =>
    request<{ plan: string[]; execution: string }>(`/workflows/${id}/execute`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancel: (id: string) =>
    request<{ execution: string }>(`/workflows/${id}/cancel`, { method: "POST" }),
  reset: (id: string, nodes: string[]) =>
    request<{ invalidated: string[] }>(`/workflows/${id}/reset`, {
      method: "POST",
      body: JSON.stringify(nodes),
    }),
  patchNode: (
    workflowId: string,
    nodeId: string,
    patch: Record<string, unknown>,
  ) =>
    request<Workflow>(`/workflows/${workflowId}/nodes/${nodeId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  createNode: (
    workflowId: string,
    node: Record<string, unknown>,
    edges: { source: string; target: string }[] = [],
  ) =>
    request<Workflow>(`/workflows/${workflowId}/nodes`, {
      method: "POST",
      body: JSON.stringify({ node, edges }),
    }),
  deleteNode: (workflowId: string, nodeId: string) =>
    request<Workflow>(`/workflows/${workflowId}/nodes/${nodeId}`, {
      method: "DELETE",
    }),
  createEdge: (workflowId: string, source: string, target: string) =>
    request<Workflow>(`/workflows/${workflowId}/edges`, {
      method: "POST",
      body: JSON.stringify({ source, target }),
    }),
  deleteEdge: (workflowId: string, source: string, target: string) =>
    request<Workflow>(
      `/workflows/${workflowId}/edges?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`,
      { method: "DELETE" },
    ),
  output: (
    workflowId: string,
    nodeId: string,
    output: string | undefined,
    page: number,
    pageSize: number,
  ) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (output) params.set("output", output);
    return request<PreviewPage>(
      `/workflows/${workflowId}/nodes/${nodeId}/output?${params.toString()}`,
    );
  },
  log: (workflowId: string, nodeId: string, after = 0) =>
    request<{ lines: LogLine[] }>(
      `/workflows/${workflowId}/nodes/${nodeId}/log?after=${after}`,
    ),
  globalLog: (after = 0) => request<{ lines: LogLine[] }>(`/logs?after=${after}`),
  lineage: (workflowId: string, nodeId: string) =>
    request<Lineage>(`/workflows/${workflowId}/lineage/${nodeId}`),
  nodeSource: (nodeTypeId: string) =>
    request<NodeSource>(`/catalog/${encodeURIComponent(nodeTypeId)}/source`),
  exportJsonUrl: (workflowId: string) =>
    `${BASE}/workflows/${workflowId}/export.json`,
  exportMermaidUrl: (workflowId: string) =>
    `${BASE}/workflows/${workflowId}/export.mermaid`,
  eventsUrl: () => `${BASE}/events`,
};

export const STATE_LABELS: Record<NodeState, string> = {
  idle: "Sin ejecutar",
  configured: "Configurado",
  running: "Ejecutando",
  executed: "Ejecutado",
  error: "Error",
};
