from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootzone_mpc.audit.thesis_evidence import write_audit


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Audit thesis evidence and claim boundaries")
    parser.add_argument(
        "--config", type=Path, default=root / "configs/thesis_evidence_audit_v1.yaml"
    )
    parser.add_argument(
        "--output", type=Path,
        default=root / "data/processed/thesis_evidence_audit_v1.json",
    )
    args = parser.parse_args()
    result = write_audit(root, args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 2)
