from pathlib import Path

from rootzone_mpc.experiments.supervision_confirmation import run_supervision_confirmation


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in run_supervision_confirmation(root):
        print(path)
