from pathlib import Path

import pandas as pd
import yaml

from rootzone_mpc.design import build_scenario_manifest, validate_scenario_manifest


def test_manifest_has_disjoint_splits_and_valid_state_order(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "configs" / "scenario_design_v1.yaml"
    output_root = tmp_path / "project"
    manifest_path, _ = build_scenario_manifest(config_path, output_root)
    frame = pd.read_csv(manifest_path)
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    audit = validate_scenario_manifest(frame, cfg)

    development_seeds = set(frame.loc[frame["split"] == "development", "scenario_seed"])
    locked_seeds = set(frame.loc[frame["split"] == "locked_evaluation", "scenario_seed"])
    assert development_seeds.isdisjoint(locked_seeds)
    assert audit["row_count"] == 168
    assert (frame["wilting_point"] < frame["initial_theta"]).all()
    assert (frame["initial_theta"] < frame["field_capacity"]).all()
