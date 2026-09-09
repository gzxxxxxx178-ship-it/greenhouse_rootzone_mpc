import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from rootzone_mpc.experiments.two_layer_identification import (
    validate_admission_precondition,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/two_layer_bridge_identification_v1.yaml"


def config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["two_layer_identification"]


def test_bridge_identification_requires_matching_passed_admission():
    cfg = config()
    binding = validate_admission_precondition(ROOT, ROOT / cfg["input"], cfg)
    assert binding["decision"] == "provisional_ready_for_identification"
    assert binding["input_sha256_matched"] is True


def test_nonready_admission_decision_blocks_fit(tmp_path):
    cfg = deepcopy(config())
    source = json.loads((ROOT / cfg["required_admission_result"]).read_text(encoding="utf-8"))
    source["model_admission_decision"] = "collect_more_excitation"
    temporary = tmp_path / "admission.json"
    temporary.write_text(json.dumps(source), encoding="utf-8")
    cfg["required_admission_result"] = str(temporary)
    with pytest.raises(ValueError, match="decision was not met"):
        validate_admission_precondition(ROOT, ROOT / cfg["input"], cfg)


def test_admission_bound_to_other_input_blocks_fit(tmp_path):
    cfg = deepcopy(config())
    source = json.loads((ROOT / cfg["required_admission_result"]).read_text(encoding="utf-8"))
    source["input_sha256"] = "0" * 64
    temporary = tmp_path / "admission.json"
    temporary.write_text(json.dumps(source), encoding="utf-8")
    cfg["required_admission_result"] = str(temporary)
    with pytest.raises(ValueError, match="not bound"):
        validate_admission_precondition(ROOT, ROOT / cfg["input"], cfg)
