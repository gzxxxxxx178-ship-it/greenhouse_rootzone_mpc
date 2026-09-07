from pathlib import Path

from rootzone_mpc.experiments.platform_resilience_sil import run_platform_resilience_sil


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in run_platform_resilience_sil(root):
        print(output)
