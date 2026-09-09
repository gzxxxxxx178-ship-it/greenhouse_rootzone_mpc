from copy import deepcopy
from pathlib import Path

import yaml

from rootzone_mpc.audit.thesis_evidence import (
    _claim_register,
    audit_repository,
    load_audit_config,
    resolve_selector,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/thesis_evidence_audit_v1.yaml"


def test_current_repository_passes_evidence_audit():
    result = audit_repository(ROOT, CONFIG_PATH)
    assert result["status"] == "passed"
    assert all(result["checks"].values())
    assert result["readiness"]["simulation_software_thesis_requirements"]["status"] == "ready"
    assert result["readiness"]["red_soil_model_requirements"]["status"] == "blocked"


def test_nested_selector_resolves_dictionary_and_list_values():
    document = {"outer": {"items": [{"value": 3.5}]}}
    assert resolve_selector(document, "outer.items.0.value") == 3.5


def test_missing_requirement_keeps_empirical_claim_conditional():
    config = load_audit_config(CONFIG_PATH)
    statuses = {"R003": "missing", "R009": "missing", "R010": "missing"}
    claims = _claim_register(config, statuses)
    assert all(item["audit_status"] == "conditional" for item in claims["conditional"])


def test_supported_claim_fails_when_required_evidence_is_missing():
    config = load_audit_config(CONFIG_PATH)
    statuses = {
        requirement: "completed"
        for claim in config["claims"]["supported"]
        for requirement in claim["requirements"]
    }
    statuses["R004"] = "missing"
    claims = _claim_register(config, statuses)
    target = next(item for item in claims["supported"] if item["claim_id"] == "C002")
    assert target["audit_status"] == "unsupported"


def test_prohibited_claims_are_never_promoted_by_status_changes():
    config = deepcopy(load_audit_config(CONFIG_PATH))
    claims = _claim_register(config, {})
    assert all(item["audit_status"] == "prohibited" for item in claims["prohibited"])


def test_missing_core_json_is_reported_without_crashing(tmp_path):
    config = load_audit_config(CONFIG_PATH)
    config["result_expectations"][0]["artifact"] = "data/processed/does_not_exist.json"
    temporary_config = tmp_path / "audit.yaml"
    temporary_config.write_text(
        yaml.safe_dump({"thesis_evidence_audit": config}, allow_unicode=True), encoding="utf-8"
    )
    result = audit_repository(ROOT, temporary_config)
    assert result["status"] == "failed"
    assert "data/processed/does_not_exist.json" in result["artifact_load_errors"]


def test_missing_manuscript_numeric_text_fails_audit(tmp_path):
    config = load_audit_config(CONFIG_PATH)
    config["manuscript_numeric_bindings"][0]["required_text"] = "不存在的论文数值文本"
    temporary_config = tmp_path / "audit.yaml"
    temporary_config.write_text(
        yaml.safe_dump({"thesis_evidence_audit": config}, allow_unicode=True), encoding="utf-8"
    )
    result = audit_repository(ROOT, temporary_config)
    assert result["status"] == "failed"
    assert result["checks"]["manuscript_numbers_match_frozen_sources"] is False
    assert result["manuscript_numeric_bindings"][0]["text_present"] is False


def test_config_binding_can_use_explicit_recorded_selector():
    config = load_audit_config(CONFIG_PATH)
    target = next(
        item for item in config["config_bindings"]
        if item["artifact"] == "data/processed/two_layer_bridge_data_v1.json"
    )
    assert target["recorded_selector"] == "protocol_config_sha256"
    result = audit_repository(ROOT, CONFIG_PATH)
    matched = next(
        item for item in result["config_bindings"]
        if item["artifact"] == target["artifact"]
    )
    assert matched["matched"] is True
