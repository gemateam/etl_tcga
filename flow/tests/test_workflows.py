from __future__ import annotations

import pytest

from tcga_flow.schemas import EdgeDefinition
from tcga_flow.workflows import (
    WorkflowError,
    ancestors,
    descendants,
    execution_plan,
    topological_order,
    validate_workflow,
)

EXPECTED_NODES = {
    "extract_cases",
    "extract_files_manifest",
    "extract_maf_manifest",
    "download_expressions",
    "bronze_cases",
    "bronze_files",
    "bronze_expressions",
    "bronze_mutations",
    "silver_clinical",
    "silver_case_expressions",
    "silver_mutations",
    "gold_top_20_diagnosis",
    "gold_sample_summary",
    "gold_gene_stats",
    "gold_gene_dim",
    "gold_marker_genes",
    "gold_sample_marker_values",
    "gold_cx_pivot",
}

EXPECTED_EDGES = {
    ("extract_cases", "bronze_cases"),
    ("extract_files_manifest", "bronze_files"),
    ("extract_files_manifest", "download_expressions"),
    ("extract_maf_manifest", "bronze_mutations"),
    ("download_expressions", "bronze_expressions"),
    ("bronze_cases", "silver_clinical"),
    ("bronze_cases", "silver_case_expressions"),
    ("bronze_files", "silver_case_expressions"),
    ("bronze_expressions", "silver_case_expressions"),
    ("bronze_mutations", "silver_mutations"),
    ("bronze_files", "silver_clinical"),
    ("silver_clinical", "silver_case_expressions"),
    ("silver_clinical", "gold_top_20_diagnosis"),
    ("silver_clinical", "gold_sample_summary"),
    ("silver_case_expressions", "gold_gene_stats"),
    ("silver_case_expressions", "gold_gene_dim"),
    ("silver_case_expressions", "gold_sample_marker_values"),
    ("silver_case_expressions", "gold_cx_pivot"),
    ("gold_gene_dim", "gold_marker_genes"),
    ("gold_marker_genes", "gold_sample_marker_values"),
}


def test_tcga_v3_shape(workflow):
    assert {node.id for node in workflow.nodes} == EXPECTED_NODES
    assert {(edge.source, edge.target) for edge in workflow.edges} == EXPECTED_EDGES
    assert len(workflow.nodes) == 18
    assert len(workflow.edges) == 20


def test_tcga_v3_is_acyclic_and_ordered(workflow):
    order = topological_order(workflow)
    position = {node_id: index for index, node_id in enumerate(order)}
    assert len(order) == 18
    for edge in workflow.edges:
        assert position[edge.source] < position[edge.target]
    assert order[0] == "extract_cases"
    assert order[-1] == "gold_cx_pivot"


def test_ancestors_and_descendants(workflow):
    assert "gold_gene_dim" in ancestors(workflow, "gold_marker_genes")
    assert "gold_marker_genes" in ancestors(workflow, "gold_sample_marker_values")
    assert "silver_case_expressions" in ancestors(workflow, "gold_cx_pivot")
    assert "bronze_files" in ancestors(workflow, "silver_clinical")
    assert descendants(workflow, "gold_gene_dim") == {
        "gold_marker_genes",
        "gold_sample_marker_values",
    }


def test_execution_plan_until_includes_ancestors(workflow):
    plan = execution_plan(workflow, until="gold_marker_genes")
    assert "gold_gene_dim" in plan
    assert "silver_case_expressions" in plan
    assert "gold_marker_genes" in plan
    assert plan.index("gold_gene_dim") < plan.index("gold_marker_genes")


def test_execution_plan_all(workflow):
    assert len(execution_plan(workflow, everything=True)) == 18


def test_unknown_node_rejected(workflow):
    with pytest.raises(WorkflowError):
        execution_plan(workflow, node_ids=["no_existe"])


def test_cycle_detected(workflow):
    broken = workflow.model_copy(deep=True)
    broken.edges.append(EdgeDefinition(source="gold_cx_pivot", target="extract_cases"))
    with pytest.raises(WorkflowError, match="ciclo"):
        validate_workflow(broken)


def test_invalid_params_rejected(workflow):
    broken = workflow.model_copy(deep=True)
    node = next(n for n in broken.nodes if n.id == "gold_cx_pivot")
    node.params = {"top_genes": 0}
    with pytest.raises(WorkflowError, match="Parámetros inválidos"):
        validate_workflow(broken)


def test_layer_mismatch_rejected(workflow):
    broken = workflow.model_copy(deep=True)
    node = next(n for n in broken.nodes if n.id == "bronze_cases")
    node.layer = "gold"
    with pytest.raises(WorkflowError, match="capa"):
        validate_workflow(broken)


def test_duplicate_edge_rejected(workflow):
    broken = workflow.model_copy(deep=True)
    broken.edges.append(EdgeDefinition(source="extract_cases", target="bronze_cases"))
    with pytest.raises(WorkflowError, match="duplicada"):
        validate_workflow(broken)


def test_missing_type_rejected(workflow):
    broken = workflow.model_copy(deep=True)
    node = next(n for n in broken.nodes if n.id == "extract_cases")
    node.type = "comando_arbitrario"
    with pytest.raises(WorkflowError, match="no registrado"):
        validate_workflow(broken)
