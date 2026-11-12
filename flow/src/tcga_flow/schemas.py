"""Esquemas Pydantic del ejecutor visual: estados, puertos, nodos y workflows."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeState(str, Enum):
    """Semáforo de un nodo, con la misma semántica que KNIME."""

    IDLE = "idle"  # rojo: sin ejecutar o sin configurar
    CONFIGURED = "configured"  # amarillo: listo para ejecutar
    RUNNING = "running"  # azul: en ejecución
    EXECUTED = "executed"  # verde
    ERROR = "error"  # rojo con mensaje


class Layer(str, Enum):
    LANDING = "landing"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class PortKind(str, Enum):
    FILES = "archivos"
    TABLE = "tabla"
    PARQUET = "parquet"
    JSON = "json"


class Port(BaseModel):
    name: str
    kind: PortKind


class OutputKind(str, Enum):
    TABLE = "table"
    PARQUET = "parquet"
    JSON = "json"


class OutputSpec(BaseModel):
    """Salida inspeccionable de un nodo (tabla PostgreSQL o fichero)."""

    key: str
    label: str
    kind: OutputKind
    target: str  # tabla ("gold.cx_pivot") o ruta/glob relativa a DATA_DIR


class Position(BaseModel):
    x: float
    y: float


class NodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    label: str
    layer: Layer
    position: Position = Field(default_factory=lambda: Position(x=0, y=0))
    params: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    status: Literal["confirmed", "inferred", "unknown"] = "confirmed"
    description: str = ""
    state: NodeState | None = None
    state_detail: str | None = None


class EdgeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    name: str
    version: int = 1
    description: str = ""
    project_id: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    nodes: list[NodeDefinition]
    edges: list[EdgeDefinition]


class NodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    notes: str | None = None
    status: Literal["confirmed", "inferred", "unknown"] | None = None
    params: dict[str, Any] | None = None
    position: Position | None = None


class CreateNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: NodeDefinition
    edges: list[EdgeDefinition] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[str] = Field(default_factory=list)
    until: str | None = None
    all: bool = False


class NodeRuntime(BaseModel):
    id: str
    state: NodeState
    detail: str | None = None
    run_id: str | None = None
    rows_in: int = 0
    rows_out: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    error: str | None = None


class WorkflowRuntime(BaseModel):
    workflow_id: str
    execution: str  # idle | running | cancelling
    current_node: str | None = None
    queue: list[str] = Field(default_factory=list)
    nodes: list[NodeRuntime]


class PreviewColumn(BaseModel):
    name: str
    type: str


class PreviewPage(BaseModel):
    output: str
    kind: OutputKind
    columns: list[PreviewColumn]
    rows: list[dict[str, Any]]
    total_rows: int | None
    page: int
    page_size: int
    size_bytes: int | None
    truncated: bool = False


class LogLine(BaseModel):
    seq: int
    ts: str
    node_id: str | None
    level: str
    message: str


class EngineMessage(BaseModel):
    type: Literal["log", "state", "execution"]
    payload: dict[str, Any]
