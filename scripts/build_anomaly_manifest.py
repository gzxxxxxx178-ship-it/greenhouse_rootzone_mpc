from pathlib import Path

from rootzone_mpc.design import build_anomaly_manifest


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in build_anomaly_manifest(root):
        print(path)
