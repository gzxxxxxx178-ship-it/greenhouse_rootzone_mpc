from pathlib import Path

import pandas as pd

from rootzone_mpc.design.supervision_v2_manifest import build_supervision_v2_manifests


def test_v2_manifests_are_balanced_and_seed_isolated(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "configs").mkdir()
    (tmp_path / "data/processed").mkdir(parents=True)
    for name in ["supervision_v2_design.yaml", "scenario_design_v1.yaml", "anomaly_design_v1.yaml"]:
        (tmp_path / "configs" / name).write_bytes((root / "configs" / name).read_bytes())
    scenario_path, anomaly_path, _ = build_supervision_v2_manifests(tmp_path)
    scenarios = pd.read_csv(scenario_path)
    anomalies = pd.read_csv(anomaly_path)
    assert scenarios.groupby("split").size().to_dict() == {
        "development": 96, "locked_evaluation": 144
    }
    counts = anomalies.groupby(["split", "anomaly_type"]).size()
    assert all(group.nunique() == 1 for _, group in counts.groupby(level=0))
    assert scenarios["scenario_seed"].is_unique
