from pathlib import Path

from rootzone_mpc.design import build_scenario_manifest


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    paths = build_scenario_manifest(root / "configs" / "scenario_design_v1.yaml", root)
    for path in paths:
        print(path)
