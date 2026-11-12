from __future__ import annotations

import pytest

from tcga_flow import catalog
from tcga_flow.runner import main as runner_main


def test_catalog_is_closed_and_complete():
    assert len(catalog.NODE_TYPES) == 44  # 18 TCGA + 18 GEMAMX-v2 granular + 8 GEMAMX-v1
    for key, node_type in catalog.NODE_TYPES.items():
        assert key == node_type.key
        schema = node_type.param_model.model_json_schema()
        assert schema["type"] == "object"


def test_catalog_rejects_unknown_type():
    with pytest.raises(KeyError):
        catalog.get_node_type("rm -rf /")


def test_param_defaults():
    params = catalog.CxPivotParams()
    assert params.top_genes == 100
    assert params.metric == "fpkm_unstranded"
    assert params.log2 is False
    assert params.sample_type is None
    assert catalog.DownloadParams().retry_failed is False


def test_param_validation_rejects_extra_fields():
    with pytest.raises(Exception):
        catalog.ProjectParams(project="TCGA-BRCA", comando="rm -rf /")


def test_runner_usage_without_arguments():
    assert runner_main([]) == 2
    assert runner_main(["extract_cases", "{no es json"]) == 2
