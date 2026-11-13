import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { toPng } from "html-to-image";
import type {
  AppConfig,
  CatalogNode,
  LogLine,
  NodeRuntime,
  Workflow,
  WorkflowRuntime,
} from "./types";
import { api } from "./api";
import { buildEdges, buildNodes, LAYER_COLORS } from "./graph";
import { nodeTypes } from "./components/Nodes";
import { LeftPanel } from "./components/LeftPanel";
import { RightPanel } from "./components/RightPanel";

function defaultParams(catalogNode: CatalogNode): Record<string, unknown> {
  const properties = catalogNode.params_schema?.properties ?? {};
  const params: Record<string, unknown> = {};
  for (const [key, property] of Object.entries(properties)) {
    if (property.default !== undefined) params[key] = property.default;
  }
  return params;
}

function FlowApp() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [catalog, setCatalog] = useState<CatalogNode[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [runtime, setRuntime] = useState<WorkflowRuntime | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [liveLogs, setLiveLogs] = useState<LogLine[]>([]);
  const [notice, setNotice] = useState<{ message: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [collapseLeft, setCollapseLeft] = useState(false);
  const [collapseRight, setCollapseRight] = useState(false);
  const wrapper = useRef<HTMLDivElement | null>(null);
  const { fitView, screenToFlowPosition } = useReactFlow();

  const showNotice = useCallback((message: string, error = false) => {
    setNotice({ message, error });
    window.setTimeout(() => setNotice(null), 5000);
  }, []);

  useEffect(() => {
    Promise.all([api.config(), api.catalog()])
      .then(([appConfig, catalogPayload]) => {
        setConfig(appConfig);
        setCatalog(catalogPayload.nodes);
        if (appConfig.workflows.length > 0) {
          setActiveId((current) => current ?? appConfig.workflows[0].id);
        }
      })
      .catch((error) => showNotice(String(error), true));
  }, [showNotice]);

  const refreshRuntime = useCallback(async (workflowId: string) => {
    try {
      const payload = await api.state(workflowId);
      setRuntime(payload);
    } catch {
      /* la UI sigue con el último estado conocido */
    }
  }, []);

  useEffect(() => {
    if (!activeId) return;
    let alive = true;
    setSelectedId(null);
    setLiveLogs([]);
    Promise.all([api.workflow(activeId), api.state(activeId)])
      .then(([definition, state]) => {
        if (!alive) return;
        setWorkflow(definition);
        setRuntime(state);
        window.setTimeout(() => fitView({ padding: 0.12, duration: 300 }), 50);
      })
      .catch((error) => showNotice(String(error), true));
    return () => {
      alive = false;
    };
  }, [activeId, fitView, showNotice]);

  useEffect(() => {
    if (!workflow || catalog.length === 0) return;
    setNodes(buildNodes(workflow, runtime, catalog));
    setEdges(buildEdges(workflow, runtime));
  }, [workflow, runtime, catalog]);

  useEffect(() => {
    if (!activeId) return;
    const source = new EventSource(api.eventsUrl());
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { type: string; payload: unknown };
        if (payload.type === "log") {
          setLiveLogs((current) => [...current.slice(-1200), payload.payload as LogLine]);
        }
        if (payload.type === "state" || payload.type === "execution") {
          void refreshRuntime(activeId);
        }
      } catch {
        /* ignora mensajes no JSON */
      }
    };
    return () => source.close();
  }, [activeId, refreshRuntime]);

  useEffect(() => {
    if (runtime?.execution !== "running") return;
    const interval = window.setInterval(() => {
      if (activeId) void refreshRuntime(activeId);
    }, 4000);
    return () => window.clearInterval(interval);
  }, [runtime?.execution, activeId, refreshRuntime]);

  // Reajustar la vista del lienzo cuando se colapsan o expanden los paneles
  useEffect(() => {
    const timer = window.setTimeout(() => {
      fitView({ padding: 0.12, duration: 250 });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [collapseLeft, collapseRight, fitView]);

  const selectedWorkflow = useMemo(
    () => config?.workflows.find((w) => w.id === activeId) ?? null,
    [config, activeId],
  );

  const selectedNode = useMemo(
    () => workflow?.nodes.find((node) => node.id === selectedId) ?? null,
    [workflow, selectedId],
  );
  const selectedRuntime: NodeRuntime | null = useMemo(
    () => runtime?.nodes.find((node) => node.id === selectedId) ?? null,
    [runtime, selectedId],
  );
  const selectedCatalog = useMemo(
    () => catalog.find((item) => item.key === selectedNode?.type) ?? null,
    [catalog, selectedNode?.type],
  );

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  const onNodeDragStop = useCallback(
    async (_event: ReactMouseEvent, node: Node) => {
      if (!workflow || node.type !== "etl") return;
      try {
        const updated = await api.patchNode(workflow.id, node.id, {
          position: { x: node.position.x, y: node.position.y },
        });
        setWorkflow(updated);
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, showNotice],
  );

  const onConnect = useCallback(
    async (connection: Connection) => {
      if (!workflow || !connection.source || !connection.target) return;
      try {
        const updated = await api.createEdge(workflow.id, connection.source, connection.target);
        setWorkflow(updated);
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, showNotice],
  );

  const onEdgesDelete = useCallback(
    async (deleted: Edge[]) => {
      if (!workflow) return;
      try {
        let updated = workflow;
        for (const edge of deleted) {
          updated = await api.deleteEdge(workflow.id, edge.source, edge.target);
        }
        setWorkflow(updated);
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, showNotice],
  );

  const onDrop = useCallback(
    async (event: ReactDragEvent) => {
      event.preventDefault();
      if (!workflow) return;
      const type = event.dataTransfer.getData("application/tcga-node");
      const catalogNode = catalog.find((item) => item.key === type);
      if (!catalogNode) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const id = `${type}_${Date.now().toString(36)}`;
      try {
        const updated = await api.createNode(workflow.id, {
          id,
          type,
          label: catalogNode.label,
          layer: catalogNode.layer,
          position,
          params: defaultParams(catalogNode),
          notes: "",
          status: "confirmed",
          description: catalogNode.description,
        });
        setWorkflow(updated);
        setSelectedId(id);
        showNotice(`Nodo ${catalogNode.label} añadido`);
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, catalog, screenToFlowPosition, showNotice],
  );

  const onNodesDelete = useCallback(
    async (deleted: Node[]) => {
      if (!workflow) return;
      try {
        let updated = workflow;
        for (const node of deleted) {
          if (node.type === "etl") {
            updated = await api.deleteNode(workflow.id, node.id);
          }
        }
        setWorkflow(updated);
        setSelectedId(null);
        showNotice("Nodo(s) eliminado(s)");
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, showNotice],
  );

  const deleteNode = useCallback(
    async (nodeId: string) => {
      if (!workflow) return;
      try {
        const updated = await api.deleteNode(workflow.id, nodeId);
        setWorkflow(updated);
        setSelectedId(null);
        showNotice("Nodo eliminado");
      } catch (error) {
        showNotice(String(error), true);
      }
    },
    [workflow, showNotice],
  );

  const runAction = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true);
      try {
        await action();
        showNotice(success);
        if (activeId) await refreshRuntime(activeId);
      } catch (error) {
        showNotice(error instanceof Error ? error.message : String(error), true);
      } finally {
        setBusy(false);
      }
    },
    [activeId, refreshRuntime, showNotice],
  );

  const executeAll = () => {
    if (!workflow) return;
    void runAction(() => api.execute(workflow.id, { all: true }), "Ejecución completa lanzada");
  };
  const executeNode = () => {
    if (!workflow || !selectedId) return;
    void runAction(
      () => api.execute(workflow.id, { nodes: [selectedId] }),
      "Nodo lanzado",
    );
  };
  const executeUntil = () => {
    if (!workflow || !selectedId) return;
    void runAction(
      () => api.execute(workflow.id, { until: selectedId }),
      "Ejecución hasta el nodo lanzada",
    );
  };
  const cancel = () => {
    if (!workflow) return;
    void runAction(() => api.cancel(workflow.id), "Cancelación solicitada");
  };
  const resetNode = () => {
    if (!workflow || !selectedId) return;
    void runAction(() => api.reset(workflow.id, [selectedId]), "Nodo invalidado");
  };
  const resetAll = () => {
    if (!workflow) return;
    if (!window.confirm("¿Invalidar todos los nodos del workflow?")) return;
    void runAction(() => api.reset(workflow.id, []), "Workflow invalidado");
  };

  const exportPng = async () => {
    const element = wrapper.current?.querySelector(".react-flow") as HTMLElement | null;
    if (!element || !workflow) return;
    try {
      const dataUrl = await toPng(element, {
        backgroundColor: "#f8fafc",
        pixelRatio: 2,
      });
      const link = document.createElement("a");
      link.download = `${workflow.id}.png`;
      link.href = dataUrl;
      link.click();
    } catch (error) {
      showNotice(String(error), true);
    }
  };

  const running = runtime?.execution === "running" || runtime?.execution === "cancelling";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand-group">
          <div className="brand">
            <span className="logo" />
            <div>
              <strong>TCGA Medallion Flow</strong>
              <small>Ejecutor visual de ETL · esquema medallón</small>
            </div>
          </div>
          {selectedWorkflow && (
            <div className="active-workflow-badge" title={selectedWorkflow.description ?? ""}>
              <span className="badge-dot" />
              <div className="badge-text">
                <span className="badge-name">{selectedWorkflow.name}</span>
                <span className="badge-meta">
                  {nodes.length} nodos · {edges.length} aristas
                  {selectedWorkflow.project_id ? ` · ${selectedWorkflow.project_id}` : ""}
                </span>
              </div>
            </div>
          )}
        </div>
        <div className="toolbar">
          <button onClick={executeAll} disabled={!workflow || busy || running}>
            Ejecutar todo
          </button>
          <button onClick={executeNode} disabled={!selectedId || busy || running}>
            Ejecutar nodo
          </button>
          <button onClick={executeUntil} disabled={!selectedId || busy || running}>
            Ejecutar hasta aquí
          </button>
          <button className="danger" onClick={cancel} disabled={!running}>
            Cancelar
          </button>
          <button onClick={resetNode} disabled={!selectedId || busy}>
            Reset nodo
          </button>
          <button onClick={resetAll} disabled={!workflow || busy}>
            Reset todo
          </button>
          <span className="separator" />
          {workflow && (
            <>
              <a className="button" href={api.exportJsonUrl(workflow.id)} download>
                JSON
              </a>
              <a className="button" href={api.exportMermaidUrl(workflow.id)} download>
                Mermaid
              </a>
              <button onClick={exportPng}>PNG</button>
            </>
          )}
          <span className="separator" />
          <button
            className={`panel-toggle-btn${collapseLeft ? " active" : ""}`}
            onClick={() => setCollapseLeft((val) => !val)}
            title="Ocultar o mostrar panel izquierdo"
          >
            {collapseLeft ? "⊞ Panel Izq" : "⊟ Panel Izq"}
          </button>
          <button
            className={`panel-toggle-btn${collapseRight ? " active" : ""}`}
            onClick={() => setCollapseRight((val) => !val)}
            title="Ocultar o mostrar panel derecho"
          >
            {collapseRight ? "⊞ Panel Der" : "⊟ Panel Der"}
          </button>
        </div>
      </header>

      {runtime && runtime.execution !== "idle" && (
        <div className="execution-banner">
          <span className={`pulse ${runtime.execution}`} />
          {runtime.execution === "cancelling" ? "Cancelando…" : "Ejecutando"}
          {runtime.current_node && <strong>{runtime.current_node}</strong>}
          {runtime.queue.length > 0 && <span>{runtime.queue.length} en cola</span>}
        </div>
      )}

      <div className={`layout${collapseLeft ? " left-collapsed" : ""}${collapseRight ? " right-collapsed" : ""}`}>
        <LeftPanel
          workflows={config?.workflows ?? []}
          activeId={activeId}
          onSelect={setActiveId}
          catalog={catalog}
          collapsed={collapseLeft}
          onToggleCollapse={() => setCollapseLeft((val) => !val)}
        />

        <main
          className="flow-canvas"
          ref={wrapper}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDrop={onDrop}
        >
          {workflow ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onNodeClick={(_event, node) => {
                if (node.type === "etl") setSelectedId(node.id);
              }}
              onNodeDragStop={onNodeDragStop}
              onConnect={onConnect}
              onEdgesDelete={onEdgesDelete}
              onNodesDelete={onNodesDelete}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              minZoom={0.25}
              maxZoom={1.6}
              proOptions={{ hideAttribution: false }}
            >
              <Background gap={24} size={1} color="#e2e8f0" />
              <Controls />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => {
                  const data = node.data as { color?: string };
                  return data.color ?? "#94a3b8";
                }}
              />
            </ReactFlow>
          ) : (
            <div className="empty-canvas">
              <p>Cargando workflow…</p>
            </div>
          )}
          <div className="legend">
            {Object.entries(LAYER_COLORS).map(([layer, color]) => (
              <span key={layer}>
                <i style={{ background: color }} /> {layer}
              </span>
            ))}
          </div>
        </main>

        {!collapseRight && (
          <RightPanel
            workflow={workflow ?? { id: "", name: "", version: 0, description: "", project_id: "", variables: {}, nodes: [], edges: [] }}
            node={selectedNode}
            catalog={selectedCatalog}
            runtime={selectedRuntime}
            liveLogs={liveLogs}
            onWorkflowChange={setWorkflow}
            onRuntimeRefresh={() => activeId && void refreshRuntime(activeId)}
            onDeleteNode={deleteNode}
            onNotice={showNotice}
            onToggleCollapse={() => setCollapseRight(true)}
          />
        )}
      </div>

      {notice && (
        <div className={`toast${notice.error ? " error" : ""}`}>{notice.message}</div>
      )}
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <FlowApp />
    </ReactFlowProvider>
  );
}
