from pathlib import Path

from rootzone_mpc.experiments import run_smoke_experiment


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    outputs = run_smoke_experiment(root / "configs" / "smoke_validation.yaml", root)
    for output in outputs:
        print(output)
