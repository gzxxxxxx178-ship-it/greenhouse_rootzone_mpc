from pathlib import Path

from rootzone_mpc.experiments.platform_sil import run_platform_sil


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in run_platform_sil(root):
        print(path)
