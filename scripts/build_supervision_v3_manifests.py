from pathlib import Path

from rootzone_mpc.design.supervision_v2_manifest import build_supervision_v3_manifests


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in build_supervision_v3_manifests(root):
        print(path)
