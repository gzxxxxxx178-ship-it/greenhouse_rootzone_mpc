from pathlib import Path

from rootzone_mpc.experiments.supervision_v3_development import run_supervision_v3_development


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in run_supervision_v3_development(root):
        print(path)
